"""Read-only project planning. Public asset URLs are never executable material."""

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.errors import ConflictError, InputValidationError
from app.models.collection_queue import CollectionQueue, CollectionWork
from app.models.project import Project, ProjectEntry, ProjectRun
from app.models.project_asset import ProjectObservation
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanRun
from app.schemas.project_incremental import ProjectIncrementalRequest
from app.services.collection_queue import CollectionQueueService, binding_for
from app.services.discovery import origin
from app.services.project_tokens import ProjectTokens
from app.services.projects import ProjectService


@dataclass
class PreparedCollectionPlan:
    token: str
    mode: str
    work: list
    validate: object


class ProjectIncrementalService:
    def __init__(self, *, queues=None, clock=None, scans=None, identities=None):
        self.queues = queues or CollectionQueueService()
        self.scans, self.identities = scans, identities
        self.tokens = ProjectTokens()
        self._confirmation_lock = threading.Lock()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def preview(self, session, request: ProjectIncrementalRequest):
        request = ProjectIncrementalRequest.model_validate(request)
        with session.no_autoflush:
            result = self._inspect(session, request)
            result["token"] = self.tokens.put(
                (request.model_dump(), self._fingerprint(session, request, result))
            )
            return result

    def _fingerprint(self, session, request, result):
        project = session.get(Project, request.project_id, populate_existing=True)
        entry = session.get(ProjectEntry, request.entry_id, populate_existing=True)
        identity = None
        if request.profile_id is not None:
            from app.models.auth_session import AuthSession
            from app.models.credential_profile import CredentialProfile

            profile = session.get(CredentialProfile, request.profile_id, populate_existing=True)
            state = session.scalar(
                select(AuthSession)
                .where(
                    AuthSession.credential_profile_id == request.profile_id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.identity_metadata.is_not(None),
                )
                .order_by(AuthSession.id.desc())
                .limit(1)
                .execution_options(populate_existing=True)
            )
            if not profile or profile.target_id != entry.target_id or not state:
                raise ConflictError("collection_identity_not_ready")
            identity = [
                [getattr(row, c.name) for c in row.__table__.columns] for row in (profile, state)
            ]
        materials = []
        for item in result["work"]:
            queue = self.queues.row(session, item["queue_id"])
            materials.append(
                [
                    queue.binding,
                    queue.revision,
                    self.queues.request(session, queue.id, item["work_id"]),
                ]
            )
        return hashlib.sha256(
            json.dumps(
                [
                    result,
                    project.revision,
                    entry.entry_url,
                    entry.target_id,
                    entry.enabled,
                    materials,
                    identity,
                ],
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()

    def start(self, session, request, token):
        # Tokens belong to this planner instance. Serialize confirmation through
        # creation/commit so a duplicate sees the committed run before revalidation.
        with self._confirmation_lock:
            return self._start(session, request, token)

    def _start(self, session, request, token):
        from app.schemas.assessment import AssessmentStartRequest
        from app.schemas.scan import ScanOptions
        from app.services.scan_application import ScanApplicationService

        request = ProjectIncrementalRequest.model_validate(request)
        saved_request, fingerprint = self.tokens.get(token)
        if saved_request != request.model_dump():
            raise ConflictError("project_incremental_preview_stale")
        key = hashlib.sha256(token.encode()).hexdigest()
        previous = session.scalar(
            select(CollectionQueue).where(CollectionQueue.resume_token == key)
        )
        if previous is not None:
            return previous.run_id

        def validate(current):
            with current.no_autoflush:
                result = self._inspect(current, request)
                if self._fingerprint(current, request, result) != fingerprint:
                    raise ConflictError("project_incremental_preview_stale")
                return result

        result = validate(session)
        if not result["work"]:
            raise InputValidationError("project_incremental_no_work")
        session.get(Project, request.project_id, populate_existing=True)
        refreshed_entry = session.get(ProjectEntry, request.entry_id, populate_existing=True)
        if refreshed_entry:
            from app.models.target import Target

            session.get(Target, refreshed_entry.target_id, populate_existing=True)
        entry = ProjectService().validate_entry(session, request.project_id, request.entry_id)
        options = ScanOptions.model_validate(result["scan_options"])
        work = []
        for item in result["work"]:
            raw = self.queues.request(session, item["queue_id"], item["work_id"])
            row = session.get(CollectionWork, item["work_id"])
            work.append(
                {
                    **raw,
                    "priority": item["priority"],
                    "depth": row.depth,
                    "asset_type": row.asset_type,
                    "not_before": row.not_before,
                }
            )
        plan = PreparedCollectionPlan(key, request.mode, work, validate)
        if request.profile_id is not None:
            from app.schemas.identity import IdentityCollectionRequest
            from app.services.identity_collection import IdentityCollectionService

            service = self.identities or IdentityCollectionService()
            return service.start(
                session,
                IdentityCollectionRequest(
                    url=entry.entry_url,
                    target_id=entry.target_id,
                    project_entry_id=entry.id,
                    profile_ids=[request.profile_id],
                    options=options,
                ),
                collection_plan=plan,
            )
        service = self.scans or ScanApplicationService()
        return service.start_assessment(
            AssessmentStartRequest(
                url=entry.entry_url,
                target_id=entry.target_id,
                project_entry_id=entry.id,
                options=options,
            ),
            collection_plan=plan,
        ).id

    def _inspect(self, session, request):
        session.get(Project, request.project_id, populate_existing=True)
        refreshed_entry = session.get(ProjectEntry, request.entry_id, populate_existing=True)
        if refreshed_entry:
            from app.models.target import Target

            session.get(Target, refreshed_entry.target_id, populate_existing=True)
        entry = ProjectService().validate_entry(session, request.project_id, request.entry_id)
        records = list(
            session.execute(
                select(ProjectObservation, ProjectRun.run_id)
                .join(ProjectRun, ProjectRun.id == ProjectObservation.project_run_id)
                .where(
                    ProjectObservation.project_id == request.project_id,
                    ProjectRun.entry_id == request.entry_id,
                )
            )
        )
        contexts = list(
            session.scalars(
                select(ScanContext).where(
                    ScanContext.credential_profile_id == request.profile_id,
                    ScanContext.kind == ("identity" if request.profile_id else "anonymous"),
                )
            )
        )
        context_ids = {context.id for context in contexts}
        records = [
            (row, run_id)
            for row, run_id in records
            if row.record.get("context_id") in context_ids
            or (row.record.get("context_id") is None and request.profile_id is None)
        ]
        important_ids = set(request.important_asset_ids)
        if important_ids - {row.display_asset_id for row, _ in records}:
            raise InputValidationError("important_asset_not_in_context")
        blocked = {
            row.display_asset_id: dict(
                asset_id=row.display_asset_id, reason="original_request_unavailable"
            )
            for row, _ in records
        }
        work = []
        seen = set()
        recent_count = 0
        now = self.clock()
        history = session.execute(
            select(CollectionWork, CollectionQueue, ScanRun)
            .join(CollectionQueue, CollectionQueue.id == CollectionWork.queue_id)
            .join(ScanRun, ScanRun.id == CollectionQueue.run_id)
            .join(ProjectRun, (ProjectRun.run_id == ScanRun.id) & (ProjectRun.source == "scan"))
            .where(
                ProjectRun.project_id == request.project_id,
                ProjectRun.entry_id == entry.id,
                ProjectRun.status == "complete",
                CollectionQueue.context_id.in_(context_ids),
            )
            .order_by(ScanRun.id.desc(), CollectionWork.id.desc())
        )
        for row, queue, run in history:
            context = session.get(ScanContext, queue.context_id, populate_existing=True)
            if binding_for(session, run, context) != queue.binding:
                raise ConflictError("collection_binding_changed")
            key = (queue.identity_key, row.request_key)
            if key in seen:
                continue
            seen.add(key)
            if row.state in {"transferred", "suppressed"}:
                continue
            try:
                raw = self.queues.request(session, queue.id, row.id)
            except InputValidationError:
                continue
            if origin(raw["url"]) != entry.origin or raw["method"] not in {"GET", "HEAD"}:
                continue
            asset_ids = {
                record.display_asset_id
                for record, source_id in records
                if source_id == run.id
                and record.record.get("context_id") == queue.context_id
                and record.record.get("method") == row.method
                and (record.record.get("route_url") or record.record.get("url")) == row.url
            }
            for asset_id in asset_ids:
                blocked.pop(asset_id, None)
            important = bool(asset_ids & important_ids)
            when = run.finished_at
            if when is not None and when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if row.state == "observed" and not important and when is not None:
                if (now - when).total_seconds() < request.stale_after_days * 86400:
                    recent_count += 1
                    continue
            reason, priority = (
                ("important", 0)
                if important
                else ("recovery", 1)
                if row.state in {"failed", "uncertain", "deferred", "leased"}
                else ("new_candidate", 2)
                if row.state == "pending"
                else ("stale_observation", 3)
            )
            base_priority = priority
            age_weeks = max(0, int((now - when).total_seconds() // (7 * 86400))) if when else 0
            priority = max(0, priority - age_weeks)
            work.append(
                dict(
                    work_id=row.id,
                    queue_id=queue.id,
                    source_run_id=run.id,
                    url=row.url,
                    method=row.method,
                    priority=priority,
                    aged=priority < base_priority,
                    reason=reason,
                    asset_ids=sorted(asset_ids),
                    last_seen=when.isoformat() if when else None,
                    not_before=self.queues.work_view(row)["not_before"],
                )
            )
        work.sort(key=lambda row: (row["priority"], row["work_id"]))
        total = len(work)
        work = work[: request.options.max_work]
        from app.schemas.scan import ScanOptions

        source = session.get(ScanRun, work[0]["source_run_id"]) if work else None
        options = ScanOptions.model_validate(source.options if source else {}).model_copy(
            update={"durable": request.options, "seed_input": "", "sitemap_url": ""}
        )
        return dict(
            scan_options=options.model_dump(),
            scope_note=(
                "仅执行已选请求；未覆盖项不表示不存在。"
                if request.mode == "known_only"
                else "在已选请求基础上继续同源发现，仍受本轮预算和范围限制。"
            ),
            project_id=request.project_id,
            entry_id=request.entry_id,
            mode=request.mode,
            identity_recheck_required=request.profile_id is not None,
            identity_health_uses_budget=request.profile_id is not None,
            work_count=len(work),
            work=work,
            blocked_count=len(blocked),
            blocked=list(blocked.values()),
            recent_count=recent_count,
            truncated=total > len(work),
            options=request.options.model_dump(),
        )
