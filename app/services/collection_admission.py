"""Durable origin admission and attempt accounting for every actual network send."""

import math
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

from sqlalchemy import func, select

from app.core.errors import ConflictError, InputValidationError, ResourceNotFoundError
from app.models.collection_queue import CollectionAttempt, CollectionGate, CollectionWork
from app.schemas.collection_queue import CollectionOptions
from app.services.collection_queue import CollectionQueueService
from app.services.collection_storage import digest
from app.services.collection_transactions import atomic_collection
from app.services.coverage_identity import redacted_observed_url
from app.services.discovery import origin
from app.services.projects import project_url


def retry_at(value, now, failures):
    try:
        if str(value).strip().isdigit():
            delay = float(str(value).strip())
            if not math.isfinite(delay):
                return float("inf")
            return now + delay
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(now, date.timestamp())
    except (TypeError, ValueError, OverflowError):
        return now + min(30, 0.5 * 2 ** min(failures, 6))


class CollectionAdmissionService:
    def __init__(self, *, queues=None, clock=time.time):
        self.clock = clock
        self.queues = queues or CollectionQueueService(clock=clock)

    def reserve(self, session, queue_id, token, url, *, method="GET", kind="http", work_id=None):
        with atomic_collection(session):
            queue = self.queues.check_lease(session, queue_id, token, sending=True)
            url, method = project_url(url), method.upper()
            if origin(url) != queue.origin or method not in {"GET", "HEAD"}:
                raise InputValidationError("collection_outside_scope")
            if work_id is not None:
                work = session.get(CollectionWork, work_id)
                if not work or work.queue_id != queue_id or work.state != "leased":
                    raise ConflictError("collection_work_not_leased")
            options = CollectionOptions(**queue.options)
            gate = session.get(CollectionGate, queue.origin, populate_existing=True)
            if gate is None:
                gate = CollectionGate(origin=queue.origin, next_send_at=0, cooldown_until=0)
                session.add(gate)
            until = max(gate.next_send_at, gate.cooldown_until)
            if until > self.clock():
                return dict(admitted=False, retry_at=until)
            used = session.scalar(
                select(func.count())
                .select_from(CollectionAttempt)
                .where(
                    CollectionAttempt.queue_id == queue_id, CollectionAttempt.state != "cancelled"
                )
            )
            if used >= options.max_requests:
                raise ConflictError("collection_request_budget")
            self.queues._bump(session, queue)
            gate.next_send_at = self.clock() + options.min_interval_ms / 1000
            attempt = CollectionAttempt(
                queue_id=queue_id,
                work_id=work_id,
                lease_token=token,
                url=redacted_observed_url(url),
                kind=kind,
                request_key=digest([url, method]),
            )
            session.add(attempt)
            if work_id is not None:
                work.attempts += 1
            session.flush()
            return dict(admitted=True, id=attempt.id)

    def _attempt(self, session, attempt_id, token, *, sending=False):
        attempt = session.get(CollectionAttempt, attempt_id, populate_existing=True)
        if attempt is None:
            raise ResourceNotFoundError("collection_attempt_missing")
        queue = self.queues.check_lease(session, attempt.queue_id, token, sending=sending)
        if attempt.lease_token != token:
            raise ConflictError("collection_lease_expired")
        return queue, attempt

    def admit(self, session, attempt_id, token):
        with atomic_collection(session):
            queue, attempt = self._attempt(session, attempt_id, token, sending=True)
            if attempt.state != "reserved":
                raise ConflictError("collection_attempt_already_admitted")
            self.queues._bump(session, queue)
            attempt.state = "possibly_sent"

    def cancel_reservation(self, session, attempt_id, token):
        with atomic_collection(session):
            queue, attempt = self._attempt(session, attempt_id, token)
            if attempt.state != "reserved":
                raise ConflictError("collection_attempt_already_admitted")
            self.queues._bump(session, queue)
            attempt.state = "cancelled"

    def finish(
        self, session, attempt_id, token, *, status_code=None, retry_after=None, evidence_bytes=0
    ):
        with atomic_collection(session):
            queue, attempt = self._attempt(session, attempt_id, token)
            if attempt.state in {"response", "failed"}:
                return
            if attempt.state != "possibly_sent":
                raise ConflictError("collection_attempt_not_admitted")
            if evidence_bytes < 0:
                raise InputValidationError("collection_invalid_evidence_size")
            used = session.scalar(
                select(func.coalesce(func.sum(CollectionAttempt.evidence_bytes), 0)).where(
                    CollectionAttempt.queue_id == queue.id
                )
            )
            if used + evidence_bytes > queue.options["max_evidence_bytes"]:
                raise ConflictError("collection_evidence_budget")
            self.queues._bump(session, queue)
            attempt.state = "response" if status_code is not None else "failed"
            attempt.status_code = status_code
            attempt.evidence_bytes = evidence_bytes
            gate = session.get(CollectionGate, queue.origin, populate_existing=True)
            if status_code is None or status_code in {429, 502, 503, 504}:
                gate.failures += 1
                gate.cooldown_until = max(
                    gate.cooldown_until,
                    retry_at(retry_after, self.clock(), gate.failures),
                    self.clock() + 30 if gate.failures >= 3 else 0,
                )
            else:
                gate.failures = 0

    def summary(self, session, queue_id):
        self.queues.row(session, queue_id)
        groups = dict(
            session.execute(
                select(CollectionAttempt.state, func.count())
                .where(CollectionAttempt.queue_id == queue_id)
                .group_by(CollectionAttempt.state)
            ).all()
        )
        kinds = dict(
            session.execute(
                select(CollectionAttempt.kind, func.count())
                .where(
                    CollectionAttempt.queue_id == queue_id, CollectionAttempt.state != "cancelled"
                )
                .group_by(CollectionAttempt.kind)
            ).all()
        )
        return dict(
            requests_used=sum(v for k, v in groups.items() if k != "cancelled"),
            possibly_sent=groups.get("possibly_sent", 0) + groups.get("reserved", 0),
            responses=groups.get("response", 0),
            by_kind=kinds,
            evidence_bytes=session.scalar(
                select(func.coalesce(func.sum(CollectionAttempt.evidence_bytes), 0)).where(
                    CollectionAttempt.queue_id == queue_id
                )
            ),
        )

    def charge_evidence(self, session, attempt_id, token, size):
        queue, attempt = self._attempt(session, attempt_id, token)
        if size < 0:
            raise InputValidationError("collection_invalid_evidence_size")
        used = session.scalar(
            select(func.coalesce(func.sum(CollectionAttempt.evidence_bytes), 0)).where(
                CollectionAttempt.queue_id == queue.id
            )
        )
        if used + size > queue.options["max_evidence_bytes"]:
            raise ConflictError("collection_evidence_budget")
        self.queues._bump(session, queue)
        attempt.evidence_bytes += size
        session.flush()
