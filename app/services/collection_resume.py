"""Read-only recovery selection; confirmation rechecks the complete local binding."""

import hashlib
import hmac
import json
from collections import Counter

from sqlalchemy import select

from app.core.errors import ConflictError, InputValidationError
from app.models.auth_session import AuthSession
from app.models.collection_queue import CollectionQueue, CollectionWork
from app.models.credential_profile import CredentialProfile
from app.models.project import Project, ProjectEntry, ProjectRun
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanRun
from app.schemas.collection_resume import CollectionResumeRequest
from app.services.collection_queue import CollectionQueueService, binding_for
from app.services.discovery import origin


class CollectionResumeService:
    def __init__(self, *, queues=None, scans=None, identities=None):
        self.queues = queues or CollectionQueueService()
        self.scans, self.identities = scans, identities

    def preview(self, session, request: CollectionResumeRequest):
        with session.no_autoflush:
            return self._inspect(session, request)

    def confirm_preview(self, session, request, token):
        preview = self.preview(session, request)
        if not isinstance(token, str) or not hmac.compare_digest(preview["token"], token):
            raise ConflictError("collection_preview_stale")
        return preview

    def _inspect(self, session, request):
        request = CollectionResumeRequest.model_validate(request)
        queue = self.queues.row(session, request.queue_id)
        if queue.lease_token and queue.lease_until > self.queues.clock():
            raise ConflictError("collection_busy")
        run = session.get(ScanRun, queue.run_id, populate_existing=True)
        context = session.get(ScanContext, queue.context_id, populate_existing=True)
        if binding_for(session, run, context) != queue.binding:
            raise ConflictError("collection_binding_changed")
        payload = self.queues.storage.read(queue.private_ref, queue.private_digest)
        rows = list(
            session.scalars(
                select(CollectionWork)
                .where(
                    CollectionWork.queue_id == queue.id,
                    CollectionWork.state.in_(
                        ["pending", "deferred", "failed", "uncertain", "leased"]
                        + (["suppressed"] if request.work_ids is not None else [])
                    ),
                )
                .order_by(CollectionWork.priority, CollectionWork.id)
            )
        )
        if request.work_ids is not None:
            selected = set(request.work_ids)
            if len(selected) != len(request.work_ids) or not selected <= {r.id for r in rows}:
                raise InputValidationError("collection_invalid_selection")
            rows = [row for row in rows if row.id in selected]
        if not rows:
            raise InputValidationError("collection_no_remaining_work")
        remaining = Counter((row.state, row.reason) for row in rows)
        recoverable, unavailable = [], []
        # Check all original materials, not only public examples, before offering confirmation.
        for row in rows:
            try:
                raw = self.queues.request(session, queue.id, row.id)
            except InputValidationError:
                unavailable.append(
                    dict(id=row.id, url=row.url, reason="private_request_unavailable")
                )
                continue
            if origin(raw["url"]) != queue.origin or raw["method"] not in {"GET", "HEAD"}:
                raise ConflictError("collection_binding_changed")
            recoverable.append(row)
        if len(recoverable) > request.options.max_work:
            raise InputValidationError("collection_selection_exceeds_budget")
        identity = None
        if context.credential_profile_id:
            profile = session.get(
                CredentialProfile, context.credential_profile_id, populate_existing=True
            )
            current = session.scalar(
                select(AuthSession)
                .where(
                    AuthSession.credential_profile_id == context.credential_profile_id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.identity_metadata.is_not(None),
                )
                .order_by(AuthSession.id.desc())
                .limit(1)
                .execution_options(populate_existing=True)
            )
            if not current or not profile or profile.target_id != run.target_id:
                raise ConflictError("collection_identity_not_ready")
            identity = [self._values(profile), self._values(current)]
        projects = []
        for source in session.scalars(
            select(ProjectRun)
            .where(ProjectRun.source == "scan", ProjectRun.run_id == run.id)
            .execution_options(populate_existing=True)
        ):
            entry = session.get(ProjectEntry, source.entry_id, populate_existing=True)
            project = session.get(Project, source.project_id, populate_existing=True)
            if not entry or not project or not entry.enabled or project.status != "active":
                raise ConflictError("collection_project_unavailable")
            if entry.target_id != run.target_id or entry.entry_url != payload["entry_url"]:
                raise ConflictError("collection_binding_changed")
            projects.append([self._values(source), self._values(entry), self._values(project)])
        binding = dict(
            request=request.model_dump(),
            revision=queue.revision,
            binding=queue.binding,
            work=[self._values(row) for row in rows],
            recoverable_work_ids=[row.id for row in recoverable],
            unavailable=unavailable,
            identity=identity,
            projects=projects,
        )
        token = hmac.new(
            queue.private_ref.encode(),
            json.dumps(binding, sort_keys=True, default=str).encode(),
            hashlib.sha256,
        ).hexdigest()
        original = self.queues.get(session, queue.id)
        return dict(
            queue_id=queue.id,
            source_run_id=run.id,
            source_context_id=context.id,
            revision=queue.revision,
            mode=request.mode,
            can_start=bool(recoverable),
            work_count=len(recoverable),
            work_ids=[row.id for row in recoverable],
            examples=[row.url for row in recoverable[:5]],
            unrecoverable_count=len(unavailable),
            unrecoverable=unavailable,
            remaining_reasons=[
                dict(state=state, reason=reason, count=count)
                for (state, reason), count in remaining.items()
            ],
            original_budget=dict(
                options=queue.options,
                requests_used=original["metrics"]["requests_used"],
                evidence_bytes=original["metrics"]["evidence_bytes"],
                work_count=sum(original["counts"].values()),
            ),
            options=request.options.model_dump(),
            token=token,
            unknown_sends=original["metrics"]["possibly_sent"],
            scope_truncated=queue.truncated,
            identity_recheck_required=identity is not None,
            identity_health_uses_budget=identity is not None,
        )

    def start(self, session, request, token):
        from app.models.scan_run import ScanRunStage
        from app.services.collection_transactions import atomic_collection
        from app.services.coverage_identity import redacted_observed_url
        from app.services.scan_application import ScanApplicationService, create_assessment_stages
        from app.services.session_storage import SessionStorageService

        request = CollectionResumeRequest.model_validate(request)
        if not isinstance(token, str) or len(token) != 64:
            raise ConflictError("collection_preview_stale")
        with atomic_collection(session):
            existing = session.scalar(
                select(CollectionQueue).where(
                    CollectionQueue.parent_id == request.queue_id,
                    CollectionQueue.resume_token == token,
                )
            )
            if existing:
                return existing.run_id
            preview = self.confirm_preview(session, request, token)
            if not preview["can_start"]:
                raise InputValidationError("collection_no_recoverable_work")
            parent = self.queues.row(session, request.queue_id)
            source = session.get(ScanRun, parent.run_id)
            old_context = session.get(ScanContext, parent.context_id)
            work = [self.queues.request(session, parent.id, wid) for wid in preview["work_ids"]]
            entry = self.queues.storage.read(parent.private_ref, parent.private_digest)["entry_url"]
            if source.mode == "quick":
                service = self.scans or ScanApplicationService()
            else:
                from app.services.identity_collection import IdentityCollectionService

                service = self.identities or IdentityCollectionService()
            service.ensure_collection_executor()
            retired = ScanApplicationService.settle_expired_collection(
                session, source.id, queues=self.queues
            )
            options = {**source.options, "durable": request.options.model_dump()}
            child = ScanRun(
                mode=source.mode,
                target_id=source.target_id,
                parent_run_id=source.id,
                recovery_context_id=old_context.id,
                input_url=redacted_observed_url(entry),
                normalized_url=source.normalized_url,
                entry_url_digest=source.entry_url_digest,
                options=options,
                status="queued",
                current_stage="queued",
                progress=0,
                completeness="pending",
                active_checks_enabled=False,
            )
            session.add(child)
            session.flush()
            context = ScanContext(
                scan_run_id=child.id,
                kind=old_context.kind,
                context_key=old_context.context_key,
                credential_profile_id=old_context.credential_profile_id,
                identity_snapshot={
                    k: v
                    for k, v in (old_context.identity_snapshot or {}).items()
                    if k in {"name", "role"}
                },
            )
            session.add(context)
            session.flush()
            if child.mode == "quick":
                create_assessment_stages(session, child)
            elif child.mode == "identity_collection":
                for index, name in enumerate(
                    ["validate", "establish_contexts", "collect", "normalize", "analyze", "report"]
                ):
                    session.add(ScanRunStage(scan_run_id=child.id, name=name, position=index + 1))
            else:
                raise InputValidationError("collection_unsupported_mode")
            child.discovery_input_ref = SessionStorageService().write_request_payload(
                child.id,
                0,
                {
                    "entry_url": entry,
                    "seed_input": "",
                    "sitemap_url": "",
                    "recovery_work": work if request.mode == "known_only" else None,
                },
            )
            view = self.queues.create(
                session, child.id, context.id, request.options, entry_url=entry, parent_id=parent.id
            )
            queue = self.queues.row(session, view["id"])
            queue.resume_token, queue.resume_mode = token, request.mode
            for wid, raw in zip(preview["work_ids"], work, strict=True):
                old = session.get(CollectionWork, wid)
                self.queues.enqueue(
                    session,
                    queue.id,
                    raw["url"],
                    method=raw["method"],
                    depth=old.depth,
                    source="recovery",
                    asset_type=old.asset_type,
                    priority=old.priority,
                    not_before=old.not_before,
                )
            self.queues._bump(session, parent)
            for wid in preview["work_ids"]:
                old = session.get(CollectionWork, wid)
                old.state, old.reason = "transferred", "linked_recovery"
            from app.schemas.projects import ProjectSourceRef
            from app.services.project_lifecycle import ProjectLifecycleService

            for membership in session.scalars(
                select(ProjectRun).where(
                    ProjectRun.source == "scan", ProjectRun.run_id == source.id
                )
            ):
                ProjectLifecycleService().attach(
                    session, membership.entry_id, ProjectSourceRef(source="scan", run_id=child.id)
                )
            child_id = child.id
        session.commit()
        if retired:
            ProjectLifecycleService().reconcile(ProjectSourceRef(source="scan", run_id=source.id))
        try:
            callback = service.execute_scan if child.mode == "quick" else service.execute
            service.dispatcher.submit(child_id, callback)
        except Exception:
            # The committed child owns the remaining work even if dispatch is unavailable.
            child = session.get(ScanRun, child_id, populate_existing=True)
            child.status, child.error_code, child.active_key = "failed", "dispatch_failed", None
            child.completeness = "incomplete"
            session.commit()
            ProjectLifecycleService().reconcile(ProjectSourceRef(source="scan", run_id=child_id))
        return child_id

    @staticmethod
    def _values(row):
        return {column.name: getattr(row, column.name) for column in row.__table__.columns}
