"""Persistent known work behind public, redacted query interfaces."""

import math
import re
import secrets
import time
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import func, select, update

from app.core.errors import ConflictError, InputValidationError, ResourceNotFoundError
from app.models.auth_session import AuthSession
from app.models.collection_queue import CollectionAttempt, CollectionQueue, CollectionWork
from app.models.credential_profile import CredentialProfile
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanRun
from app.models.target import Target
from app.schemas.collection_queue import CollectionOptions
from app.services.collection_storage import CollectionStorage, digest
from app.services.collection_timing import initial_telemetry, sample_time
from app.services.collection_transactions import atomic_collection
from app.services.coverage_identity import redacted_observed_url
from app.services.discovery import SOURCE_KINDS, origin, safe_route
from app.services.projects import project_url


def binding_for(session, run, context):
    target = session.get(Target, run.target_id, populate_existing=True) if run.target_id else None
    profile = (
        session.get(CredentialProfile, context.credential_profile_id, populate_existing=True)
        if context.credential_profile_id
        else None
    )
    return digest(
        dict(
            target=[target.id, target.base_url, target.type, target.status] if target else None,
            profile=[profile.id, profile.target_id, profile.secret_ref, profile.login_config_path]
            if profile
            else None,
            options=run.options,
        )
    )


def identity_binding_for(session, context):
    identity_binding = {}
    if context.kind == "identity":
        stored = session.scalar(
            select(AuthSession)
            .where(
                AuthSession.credential_profile_id == context.credential_profile_id,
                AuthSession.identity_metadata.is_not(None),
                AuthSession.revoked_at.is_(None),
            )
            .order_by(AuthSession.id.desc())
            .execution_options(populate_existing=True)
        )
        # Validation timestamps/status change during collection and are not identity material.
        identity_binding["restored_identity"] = (
            [
                stored.id,
                stored.target_id,
                stored.storage_ref,
                stored.identity_metadata,
                stored.expires_at.isoformat() if stored.expires_at else None,
            ]
            if stored
            else None
        )
    return digest(identity_binding)


class CollectionQueueService:
    def __init__(self, *, storage=None, clock=time.time):
        self.storage = storage or CollectionStorage()
        self.clock = clock

    def row(self, session, queue_id):
        queue = session.get(CollectionQueue, queue_id, populate_existing=True)
        if queue is None:
            raise ResourceNotFoundError("collection_queue_missing")
        return queue

    def create(self, session, run_id, context_id, options, *, entry_url=None, parent_id=None):
        with atomic_collection(session):
            run = session.get(ScanRun, run_id)
            context = session.get(ScanContext, context_id)
            if not run or not context or context.scan_run_id != run_id:
                raise InputValidationError("collection_context_mismatch")
            existing = session.scalar(
                select(CollectionQueue).where(CollectionQueue.context_id == context_id)
            )
            if existing:
                return self.get(session, existing.id)
            if context.kind == "identity":
                context.identity_snapshot = {
                    **(context.identity_snapshot or {}),
                    "collection_identity_binding": identity_binding_for(session, context),
                }
            entry = project_url(entry_url or run.normalized_url)
            ref, proof = self.storage.write({"version": 1, "entry_url": entry})
            queue = CollectionQueue(
                run_id=run_id,
                context_id=context_id,
                origin=origin(entry),
                identity_key=f"target:{run.target_id}:profile:{context.credential_profile_id}"
                if context.credential_profile_id
                else "anonymous",
                options=options.model_dump(),
                telemetry=initial_telemetry(self.clock()),
                binding=binding_for(session, run, context),
                private_ref=ref,
                private_digest=proof,
                parent_id=parent_id,
            )
            session.add(queue)
            session.flush()
            return self.get(session, queue.id)

    def get(self, session, queue_id):
        queue = self.row(session, queue_id)
        from app.services.collection_admission import CollectionAdmissionService

        counts = dict(
            session.execute(
                select(CollectionWork.state, func.count())
                .where(CollectionWork.queue_id == queue_id)
                .group_by(CollectionWork.state)
            ).all()
        )
        return dict(
            id=queue.id,
            run_id=queue.run_id,
            context_id=queue.context_id,
            parent_id=queue.parent_id,
            status=queue.status,
            revision=queue.revision,
            origin=queue.origin,
            identity_key=queue.identity_key,
            options=queue.options,
            counts=counts,
            metrics=CollectionAdmissionService(queues=self, clock=self.clock).summary(
                session, queue_id
            ),
            truncated=queue.truncated,
            coverage_complete=bool(counts) and not queue.truncated and set(counts) <= {"observed"},
        )

    def work_view(self, row):
        result = {
            key: getattr(row, key)
            for key in (
                "id",
                "queue_id",
                "url",
                "method",
                "state",
                "reason",
                "depth",
                "priority",
                "sources",
                "attempts",
                "not_before",
                "asset_type",
            )
        }

        if not math.isfinite(result["not_before"]):
            result["not_before"] = None
            result["retry_time_unrepresentable"] = True
        return result

    def list_work(self, session, queue_id, *, state=None, page=1, page_size=50):
        self.row(session, queue_id)
        if page < 1 or not 1 <= page_size <= 200:
            raise InputValidationError("collection_invalid_page")
        query = select(CollectionWork).where(CollectionWork.queue_id == queue_id)
        if state:
            query = query.where(CollectionWork.state == state)
        count = session.scalar(select(func.count()).select_from(query.subquery()))
        items = session.scalars(
            query.order_by(CollectionWork.id).offset((page - 1) * page_size).limit(page_size)
        )
        return dict(
            total=count,
            page=page,
            page_size=page_size,
            items=[self.work_view(row) for row in items],
        )

    def request(self, session, queue_id, work_id):
        self.row(session, queue_id)
        work = session.get(CollectionWork, work_id)
        if not work or work.queue_id != queue_id:
            raise ResourceNotFoundError("collection_work_missing")
        return self.storage.read(work.private_ref, work.private_digest)

    def enqueue(
        self,
        session,
        queue_id,
        url,
        *,
        method="GET",
        depth=0,
        source="entry",
        priority=2,
        not_before=0.0,
        asset_type="page",
        max_depth=None,
    ):
        if math.isnan(not_before) or not_before < 0:
            raise InputValidationError("collection_invalid_retry_time")
        with atomic_collection(session):
            queue = self.row(session, queue_id)
            if queue.status not in {"pending", "running", "paused"}:
                raise ConflictError("collection_not_accepting_work")
            url = project_url(url)
            method = method.upper()
            if origin(url) != queue.origin or method not in {"GET", "HEAD"}:
                raise InputValidationError("collection_outside_scope")
            key = digest([url, method])
            existing = session.scalar(
                select(CollectionWork).where(
                    CollectionWork.queue_id == queue_id, CollectionWork.request_key == key
                )
            )
            source = (
                source
                if source in SOURCE_KINDS or source in {"history", "recovery", "sitemap_index"}
                else "unknown"
            )
            if existing:
                new_source = source not in existing.sources and len(existing.sources) < 20
                depth_limited = (
                    max_depth is not None
                    and existing.depth > max_depth
                    and existing.state == "pending"
                )
                if queue.telemetry is not None or new_source or depth_limited:
                    self._bump(session, queue)
                if queue.telemetry is not None:
                    queue.telemetry = {
                        **queue.telemetry,
                        "duplicate_work_submissions": queue.telemetry["duplicate_work_submissions"]
                        + 1,
                    }
                if new_source:
                    existing.sources = [*existing.sources, source]
                if depth_limited:
                    existing.state, existing.reason = "suppressed", "max_depth"
                session.flush()
                return self.work_view(existing)
            options = CollectionOptions(**queue.options)
            if (
                session.scalar(
                    select(func.count())
                    .select_from(CollectionWork)
                    .where(CollectionWork.queue_id == queue_id)
                )
                >= options.max_work
            ):
                self._bump(session, queue)
                queue.truncated = True
                session.flush()
                return dict(id=None, state="suppressed", reason="max_work")
            parsed = urlsplit(url)
            family = re.sub(r"/\d+(?=/|$)|/\d{4}-\d{2}-\d{2}(?=/|$)", "/{number}", parsed.path)
            route = digest(
                [family, sorted(k for k, _ in parse_qsl(parsed.query)), method, safe_route(url)]
            )
            variants = session.scalar(
                select(func.count())
                .select_from(CollectionWork)
                .where(CollectionWork.queue_id == queue_id, CollectionWork.route_key == route)
            )
            ref, proof = self.storage.write({"url": url, "method": method})
            suppression = (
                "max_variants"
                if variants >= options.max_variants
                else "max_depth"
                if max_depth is not None and depth > max_depth
                else None
            )
            work = CollectionWork(
                queue_id=queue_id,
                request_key=key,
                route_key=route,
                url=safe_route(url) or redacted_observed_url(url),
                method=method,
                asset_type=asset_type,
                private_ref=ref,
                private_digest=proof,
                depth=depth,
                priority=priority,
                sources=[source],
                state=(
                    "suppressed"
                    if suppression
                    else "deferred"
                    if not_before > self.clock()
                    else "pending"
                ),
                not_before=not_before,
                reason=suppression,
            )
            session.add(work)
            self._bump(session, queue)
            session.flush()
            return self.work_view(work)

    def _bump(self, session, queue):
        revision = queue.revision
        changed = session.execute(
            update(CollectionQueue)
            .where(CollectionQueue.id == queue.id, CollectionQueue.revision == revision)
            .values(revision=revision + 1)
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise ConflictError("collection_concurrent_update")
        session.refresh(queue)

    def lease(self, session, queue_id):
        with atomic_collection(session):
            queue = self.row(session, queue_id)
            if queue.status != "pending" or queue.lease_token:
                raise ConflictError("collection_busy")
            active = list(
                session.scalars(
                    select(CollectionQueue).where(
                        CollectionQueue.origin == queue.origin,
                        CollectionQueue.lease_token.is_not(None),
                        CollectionQueue.lease_until > self.clock(),
                    )
                )
            )
            cap = min(
                [
                    queue.options["origin_concurrency"],
                    *[row.options["origin_concurrency"] for row in active],
                ]
            )
            if len(active) >= cap or any(row.identity_key == queue.identity_key for row in active):
                raise ConflictError("collection_origin_busy")
            self._bump(session, queue)
            queue.lease_token = secrets.token_hex(32)
            queue.lease_until = self.clock() + 120
            queue.status = "running"
            sample_time(queue, self.clock(), start=True)
            session.flush()
            return queue.lease_token

    def check_lease(self, session, queue_id, token, *, sending=False):
        queue = self.row(session, queue_id)
        if not token or token != queue.lease_token or queue.lease_until <= self.clock():
            raise ConflictError("collection_lease_expired")
        if sending and queue.status != "running":
            raise ConflictError("collection_paused")
        run = session.get(ScanRun, queue.run_id, populate_existing=True)
        context = session.get(ScanContext, queue.context_id, populate_existing=True)
        if binding_for(session, run, context) != queue.binding:
            raise ConflictError("collection_binding_changed")
        if context.kind == "identity" and identity_binding_for(session, context) != (
            context.identity_snapshot or {}
        ).get("collection_identity_binding"):
            raise ConflictError("collection_identity_binding_changed")
        return queue

    def heartbeat(self, session, queue_id, token):
        with atomic_collection(session):
            queue = self.check_lease(session, queue_id, token)
            self._bump(session, queue)
            queue.lease_until = self.clock() + 120
            sample_time(queue, self.clock())

    def set_waiting(self, session, queue_id, token, reason):
        if reason not in {None, "rate_limit", "cooldown"}:
            raise InputValidationError("collection_invalid_wait_reason")
        with atomic_collection(session):
            queue = self.check_lease(session, queue_id, token)
            self._bump(session, queue)
            sample_time(queue, self.clock(), waiting=reason)

    def claim(self, session, queue_id, token):
        with atomic_collection(session):
            queue = self.check_lease(session, queue_id, token)
            if queue.status != "running":
                return None
            self._bump(session, queue)
            work = session.scalar(
                select(CollectionWork)
                .where(
                    CollectionWork.queue_id == queue_id,
                    CollectionWork.state.in_(["pending", "deferred"]),
                    CollectionWork.not_before <= self.clock(),
                )
                .order_by(CollectionWork.priority, CollectionWork.id)
                .limit(1)
            )
            if work is None:
                return None
            work.state = "leased"
            session.flush()
            return self.work_view(work)

    def claim_requested(self, session, queue_id, work_id, token):
        """Bind an intercepted browser request without bypassing lease or retry time."""
        with atomic_collection(session):
            queue = self.check_lease(session, queue_id, token, sending=True)
            work = session.get(CollectionWork, work_id, populate_existing=True)
            if not work or work.queue_id != queue_id:
                raise ResourceNotFoundError("collection_work_missing")
            if work.state in {"pending", "deferred"}:
                if work.not_before > self.clock():
                    raise ConflictError("collection_retry_pending")
                self._bump(session, queue)
                work.state = "leased"
            session.flush()
            return self.work_view(work)

    def complete(self, session, queue_id, work_id, token, *, attempt_id=None):
        with atomic_collection(session):
            queue = self.check_lease(session, queue_id, token)
            work = session.get(CollectionWork, work_id)
            if not work or work.queue_id != queue_id:
                raise ResourceNotFoundError("collection_work_missing")
            if work.state == "observed":
                return self.work_view(work)
            if work.state != "leased":
                attempt = session.get(CollectionAttempt, attempt_id) if attempt_id else None
                if not (
                    work.state in {"failed", "deferred"}
                    and attempt
                    and attempt.queue_id == queue_id
                    and attempt.work_id == work_id
                    and attempt.state == "response"
                    and attempt.status_code not in {429, 502, 503, 504}
                ):
                    raise ConflictError("collection_work_not_leased")
            self._bump(session, queue)
            work.state, work.reason = "observed", None
            session.flush()
            return self.work_view(work)

    def assess_identity(self, session, queue_id, token, url, method, assessment):
        with atomic_collection(session):
            queue = self.check_lease(session, queue_id, token)
            work = session.scalar(
                select(CollectionWork).where(
                    CollectionWork.queue_id == queue_id,
                    CollectionWork.request_key == digest([project_url(url), method]),
                )
            )
            if work is None or work.state in {"deferred", "failed"}:
                return
            confirmed = assessment == "confirmed"
            if work.reason == "identity_confirmed" and not confirmed:
                return
            self._bump(session, queue)
            work.state = "observed" if confirmed else "uncertain"
            work.reason = "identity_confirmed" if confirmed else "identity_uncertain"

    def pause(self, session, queue_id):
        with atomic_collection(session):
            queue = self.row(session, queue_id)
            self._bump(session, queue)
            queue.status = "paused"
            session.flush()
            return self.get(session, queue_id)

    def recover_expired(self, session, *, run_id=None):
        """Recover expired leases; a targeted run must have no live sibling lease."""
        with atomic_collection(session):
            if run_id is not None:
                # Obtain the SQLite writer lock before validating sibling leases.
                session.execute(
                    update(CollectionQueue)
                    .where(CollectionQueue.run_id == run_id)
                    .values(revision=CollectionQueue.revision + 1)
                )
                siblings = list(
                    session.scalars(
                        select(CollectionQueue)
                        .where(CollectionQueue.run_id == run_id)
                        .execution_options(populate_existing=True)
                    )
                )
                if not any(q.lease_token for q in siblings) or any(
                    q.lease_token and q.lease_until > self.clock() for q in siblings
                ):
                    raise ConflictError("collection_source_active")
            query = select(CollectionQueue).where(
                CollectionQueue.lease_token.is_not(None),
                CollectionQueue.lease_until <= self.clock(),
            )
            if run_id is not None:
                query = query.where(CollectionQueue.run_id == run_id)
            expired = list(session.scalars(query))
            for queue in expired:
                self._bump(session, queue)
                session.execute(
                    update(CollectionWork)
                    .where(CollectionWork.queue_id == queue.id, CollectionWork.state == "leased")
                    .values(state="uncertain", reason="worker_lost")
                )
                queue.status, queue.lease_token = "paused", None
            return len(expired)

    def for_run(self, session, run_id):
        ids = list(
            session.scalars(
                select(CollectionQueue.id)
                .where(CollectionQueue.run_id == run_id)
                .order_by(CollectionQueue.id)
            )
        )
        return [self.get(session, qid) for qid in ids]

    def stop_work(self, session, queue_id, work_id, token, reason, *, retry_at=None):
        with atomic_collection(session):
            queue = self.check_lease(session, queue_id, token)
            work = session.get(CollectionWork, work_id)
            if not work or work.queue_id != queue_id or work.state == "observed":
                raise ConflictError("collection_work_not_leased")
            self._bump(session, queue)
            work.reason = reason
            work.state = "deferred" if retry_at is not None else "failed"
            work.not_before = retry_at or 0
            session.flush()

    def close(self, session, queue_id, token):
        with atomic_collection(session):
            queue = self.check_lease(session, queue_id, token)
            self._bump(session, queue)
            session.execute(
                update(CollectionWork)
                .where(CollectionWork.queue_id == queue_id, CollectionWork.state == "leased")
                .values(state="uncertain", reason="collection_stopped")
            )
            queue.status = "paused" if queue.status == "paused" else "finished"
            queue.lease_token = None
            sample_time(queue, self.clock(), complete=True)
            session.flush()
            return self.get(session, queue_id)


def prepare_queues(session, run, options, entry_url, *, plan=None):
    if options.durable is None:
        return
    service = CollectionQueueService()
    session.flush()
    contexts = list(session.scalars(select(ScanContext).where(ScanContext.scan_run_id == run.id)))
    for context in contexts:
        queue = service.create(session, run.id, context.id, options.durable, entry_url=entry_url)
        if plan is not None:
            row = service.row(session, queue["id"])
            row.resume_token, row.resume_mode = plan.token, plan.mode
            for item in plan.work:
                service.enqueue(
                    session,
                    row.id,
                    item["url"],
                    method=item["method"],
                    priority=item["priority"],
                    not_before=item["not_before"],
                    depth=item["depth"],
                    asset_type=item["asset_type"],
                    source="recovery",
                )
            continue
        service.enqueue(session, queue["id"], entry_url, priority=0)
        from urllib.parse import urljoin

        from app.services.discovery_sources import parse_seed_input

        if options.sitemap_enabled:
            url = options.sitemap_url or urljoin(entry_url, "/sitemap.xml")
            if origin(url) == origin(entry_url):
                service.enqueue(session, queue["id"], url, source="sitemap", asset_type="sitemap")
        if options.seed_input:
            for seed in parse_seed_input(
                options.seed_input, options.seed_format, base_url=entry_url
            ):
                if seed.get("auto_visit", True) and origin(seed["url"]) == origin(entry_url):
                    service.enqueue(
                        session,
                        queue["id"],
                        seed["url"],
                        source=seed["source_kind"],
                        asset_type=seed["asset_type"],
                    )
