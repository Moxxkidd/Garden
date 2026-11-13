"""Single-process, fixed-hour scheduler using existing bounded task execution."""

import hashlib
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.errors import ConflictError, GardenError, ResourceNotFoundError
from app.core.settings import get_settings
from app.db.bootstrap import session_scope
from app.models.auth_session import AuthSession
from app.models.credential_profile import CredentialProfile
from app.models.inventory_run import InventoryRun
from app.models.project import ProjectRun
from app.models.project_curation import ProjectAudit
from app.models.project_schedule import ProjectSchedule
from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES, ScanRun
from app.models.target import Target
from app.schemas.assessment import AssessmentStartRequest
from app.schemas.identity import IdentityCollectionRequest
from app.schemas.project_schedule import ScheduleRequest
from app.schemas.scan import ScanOptions
from app.services.collection_executor import executor_operation
from app.services.project_freshness import utc
from app.services.project_source import digest
from app.services.project_tokens import ProjectTokens
from app.services.projects import ProjectService
from app.services.scan_options import resolve_scan_options


def pause_schedules(session, project_id, reason):
    for row in session.scalars(
        select(ProjectSchedule).where(
            ProjectSchedule.project_id == project_id, ProjectSchedule.enabled.is_(True)
        )
    ):
        row.enabled, row.last_result = False, reason
        session.add(
            ProjectAudit(
                project_id=project_id,
                action="schedule_event",
                detail=dict(schedule_id=row.id, result=reason),
            )
        )


class ProjectScheduleService:
    def __init__(self, *, scans=None, identities=None, clock=None):
        self.scans, self.identities = scans, identities
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.tokens = ProjectTokens()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None

    def _binding(self, session, project_id, request):
        entry = ProjectService().validate_entry(session, project_id, request.entry_id)
        target = session.get(Target, entry.target_id, populate_existing=True)
        material = [
            entry.id,
            entry.target_id,
            entry.name,
            entry.entry_url,
            entry.enabled,
            target.base_url,
            target.status,
        ]
        if request.profile_id is not None:
            profile = session.get(CredentialProfile, request.profile_id, populate_existing=True)
            state = session.scalar(
                select(AuthSession)
                .where(
                    AuthSession.credential_profile_id == request.profile_id,
                    AuthSession.identity_metadata.is_not(None),
                )
                .order_by(AuthSession.id.desc())
                .limit(1)
                .execution_options(populate_existing=True)
            )
            if (
                not profile
                or profile.target_id != entry.target_id
                or not state
                or state.status != "active"
                or state.revoked_at
                or not state.last_validated_at
                or (state.expires_at and utc(state.expires_at) <= utc(self.clock()))
            ):
                raise ConflictError("schedule_identity_not_ready")
            config = Path(profile.login_config_path)
            if not config.is_file() or not Path(state.storage_ref).is_file():
                raise ConflictError("schedule_identity_material_unavailable")
            material.extend(
                [
                    dict(
                        (c.name, getattr(profile, c.name))
                        for c in profile.__table__.columns
                        if c.name not in {"created_at", "updated_at"}
                    ),
                    hashlib.sha256(config.read_bytes()).hexdigest(),
                    state.id,
                    state.identity_metadata,
                ]
            )
        return digest(material), entry

    def preview(self, session, project_id, request, *, schedule_id=None):
        request = ScheduleRequest.model_validate(request)
        options = resolve_scan_options(request.options, get_settings())
        if request.profile_id is not None:
            options = options.model_copy(update={"collection_mode": "browser"})
        request = request.model_copy(update={"options": options})
        binding, entry = self._binding(session, project_id, request)
        if schedule_id is not None:
            self.get(session, project_id, schedule_id)
        token = self.tokens.put((project_id, schedule_id, request, binding))
        from app.services.coverage_identity import redacted_observed_url

        return dict(
            token=token,
            project_id=project_id,
            schedule_id=schedule_id,
            entry_id=entry.id,
            url=redacted_observed_url(entry.entry_url),
            profile_id=request.profile_id,
            start_at=utc(request.start_at).isoformat(),
            interval_hours=request.interval_hours,
            options=options.model_dump(mode="json"),
            note=(
                "开启后会在本地服务存活时自动发送请求；仅绑定入口同源 GET/HEAD，"
                "身份验证也计入预算。停机错过不补发。"
            ),
        )

    def confirm(self, session, token, *, enable=False):
        with self.tokens.lock:
            pid, sid, request, binding = self.tokens.get(token)
            current, _ = self._binding(session, pid, request)
            if binding != current:
                raise ConflictError("schedule_preview_changed")
            row = self.get(session, pid, sid) if sid else ProjectSchedule(project_id=pid)
            row.entry_id, row.profile_id = request.entry_id, request.profile_id
            row.configuration = request.options.model_dump(mode="json")
            row.interval_hours, row.start_at = request.interval_hours, utc(request.start_at)
            row.next_at = self._future(
                row.start_at, row.interval_hours, utc(self.clock()), inclusive=True
            )
            row.enabled, row.binding = enable, binding
            row.last_result = "enabled" if enable else "disabled"
            session.add(row)
            session.flush()
            self._event(session, row, row.last_result)
            self.tokens.values.pop(token)
            return self.view(row)

    def get(self, session, pid, sid):
        row = session.get(ProjectSchedule, sid)
        if not row or row.project_id != pid:
            raise ResourceNotFoundError("project_schedule_not_found")
        return row

    def list(self, session, pid):
        ProjectService().get(session, pid)
        return [
            self.view(r)
            for r in session.scalars(
                select(ProjectSchedule)
                .where(ProjectSchedule.project_id == pid)
                .order_by(ProjectSchedule.id)
            )
        ]

    def view(self, row):
        return dict(
            id=row.id,
            project_id=row.project_id,
            entry_id=row.entry_id,
            profile_id=row.profile_id,
            enabled=row.enabled,
            interval_hours=row.interval_hours,
            start_at=utc(row.start_at).isoformat(),
            next_at=utc(row.next_at).isoformat(),
            configuration=row.configuration,
            last_at=utc(row.last_at).isoformat() if row.last_at else None,
            last_result=row.last_result,
            last_run_id=row.last_run_id,
        )

    def pause(self, session, pid, sid):
        row = self.get(session, pid, sid)
        row.enabled = False
        self._event(session, row, "paused")
        return self.view(row)

    def history(self, session, pid, sid):
        self.get(session, pid, sid)
        return [
            dict(at=utc(r.created_at).isoformat(), **r.detail)
            for r in session.scalars(
                select(ProjectAudit)
                .where(
                    ProjectAudit.project_id == pid,
                    ProjectAudit.action == "schedule_event",
                    ProjectAudit.detail["schedule_id"].as_integer() == sid,
                )
                .order_by(ProjectAudit.id.desc())
                .limit(100)
            )
        ]

    def _event(self, session, row, result, **detail):
        row.last_at, row.last_result = utc(self.clock()), result
        session.add(
            ProjectAudit(
                project_id=row.project_id,
                action="schedule_event",
                detail=dict(schedule_id=row.id, result=result, **detail),
            )
        )

    @staticmethod
    def _future(start, hours, now, *, inclusive=False):
        step = timedelta(hours=hours)
        if start > now or (inclusive and start == now):
            return start
        return start + (int((now - start) // step) + 1) * step

    def startup(self):
        with self.lock, executor_operation(get_settings().database_url), session_scope() as session:
            for row in session.scalars(
                select(ProjectSchedule).where(ProjectSchedule.enabled.is_(True))
            ):
                if utc(row.next_at) <= utc(self.clock()):
                    row.next_at = self._future(
                        utc(row.next_at), row.interval_hours, utc(self.clock())
                    )
                    self._event(session, row, "missed")

    def tick(self):
        with self.lock, executor_operation(get_settings().database_url), session_scope() as session:
            if self.stop.is_set():
                return
            rows = list(
                session.scalars(
                    select(ProjectSchedule)
                    .where(ProjectSchedule.enabled.is_(True))
                    .order_by(ProjectSchedule.id)
                )
            )
            for row in rows:
                request = ScheduleRequest(
                    entry_id=row.entry_id,
                    profile_id=row.profile_id,
                    start_at=utc(row.start_at),
                    interval_hours=row.interval_hours,
                    options=row.configuration,
                )
                try:
                    binding, entry = self._binding(session, row.project_id, request)
                    if binding != row.binding:
                        raise ConflictError("schedule_binding_changed")
                except GardenError as error:
                    row.enabled = False
                    reason = (
                        str(error)
                        if str(error).startswith("schedule_")
                        else "schedule_binding_changed"
                    )
                    self._event(session, row, reason)
                    session.commit()
                    continue
                if utc(row.next_at) > utc(self.clock()):
                    continue
                due = utc(row.next_at)
                row.next_at = self._future(due, row.interval_hours, utc(self.clock()))
                active = session.scalar(
                    select(ScanRun.id)
                    .join(
                        ProjectRun,
                        (ProjectRun.source == "scan") & (ProjectRun.run_id == ScanRun.id),
                    )
                    .where(
                        ProjectRun.project_id == row.project_id,
                        ScanRun.status.not_in(TERMINAL_SCAN_RUN_STATUSES),
                    )
                    .limit(1)
                )
                if not active:
                    active = session.scalar(
                        select(InventoryRun.id)
                        .join(
                            ProjectRun,
                            (ProjectRun.source == "inventory")
                            & (ProjectRun.run_id == InventoryRun.id),
                        )
                        .where(
                            ProjectRun.project_id == row.project_id,
                            InventoryRun.status.in_(["pending", "queued", "running"]),
                        )
                        .limit(1)
                    )
                if active:
                    self._event(
                        session, row, "skipped_active", scheduled_for=due.isoformat(), run_id=active
                    )
                    session.commit()
                    continue
                # Persist the consumed occurrence BEFORE handing it to the existing task service.
                # A crash cannot turn an already claimed occurrence into a restart catch-up send.
                self._event(session, row, "claimed", scheduled_for=due.isoformat())
                session.commit()
                try:
                    options = ScanOptions.model_validate(row.configuration)
                    from app.services.project_incremental import PreparedCollectionPlan

                    def validate(current, row=row, request=request):
                        if self._binding(current, row.project_id, request)[0] != row.binding:
                            raise ConflictError("schedule_binding_changed")

                    plan = PreparedCollectionPlan(
                        digest([row.id, due.isoformat()]), "continue_discovery", [], validate
                    )
                    if row.profile_id is not None:
                        run_id = self.identities.start(
                            session,
                            IdentityCollectionRequest(
                                url=entry.entry_url,
                                target_id=entry.target_id,
                                project_entry_id=entry.id,
                                profile_ids=[row.profile_id],
                                options=options,
                            ),
                            collection_plan=plan,
                        )
                    else:
                        run_id = self.scans.start_assessment(
                            AssessmentStartRequest(
                                url=entry.entry_url,
                                target_id=entry.target_id,
                                project_entry_id=entry.id,
                                options=options,
                            ),
                            collection_plan=plan,
                        ).id
                    row.last_run_id = run_id
                    self._event(session, row, "started", run_id=run_id)
                except GardenError:
                    session.rollback()
                    row.enabled = False
                    self._event(session, row, "start_failed")
                session.commit()

    def start(self):
        self.startup()

        def loop():
            from app.core.logging import get_logger

            while not self.stop.wait(1):
                try:
                    self.tick()
                except Exception:
                    get_logger(__name__).exception(
                        "Local recheck scheduler stopped; explicit restart required"
                    )
                    self.stop.set()

        self.thread = threading.Thread(target=loop, name="garden-rechecks", daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread:
            self.thread.join()
