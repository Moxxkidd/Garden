"""Transport adapter for short durable transactions between network operations."""

import json
import time

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models.collection_queue import CollectionQueue
from app.models.scan_run import ScanEvidence
from app.services.collection_admission import CollectionAdmissionService
from app.services.collection_queue import CollectionQueueService
from app.services.collection_transactions import atomic_collection


class CollectionStopped(RuntimeError):
    pass


class CollectionRuntime:
    def __init__(self, pipeline, session, run, context, options, deadline):
        self.pipeline, self.session, self.run = pipeline, session, run
        self.context, self.options, self.deadline = context, options, deadline
        self.queues = CollectionQueueService()
        self.admission = CollectionAdmissionService(queues=self.queues)
        self.queue_id = session.scalar(
            select(CollectionQueue.id).where(CollectionQueue.context_id == context.id)
        )
        if self.queue_id is None:
            raise ConflictError("collection_queue_missing")
        self.token = None
        self.last_heartbeat = 0
        while self.token is None:
            self.pipeline._guard_network_request(session, run.id, deadline)
            try:
                self.token = self.queues.lease(session, self.queue_id)
                session.commit()
            except ConflictError as error:
                session.rollback()
                if str(error) != "collection_origin_busy":
                    raise
                time.sleep(0.05)
        self.latest_attempt = None
        self.work_id = None

    def check(self):
        self.pipeline._guard_network_request(self.session, self.run.id, self.deadline)
        self.queues.check_lease(self.session, self.queue_id, self.token, sending=True)
        if time.time() - self.last_heartbeat >= 10:
            self.queues.heartbeat(self.session, self.queue_id, self.token)
            self.session.commit()
            self.last_heartbeat = time.time()

    def wait_until(self, timestamp):
        if timestamp - time.time() >= self.deadline - self.pipeline.clock():
            raise CollectionStopped("backoff_exceeds_deadline")
        while time.time() < timestamp:
            self.check()
            self.session.commit()
            time.sleep(min(0.05, max(0, timestamp - time.time())))

    def before_send(self, url, method="GET", kind="http"):
        while True:
            self.check()
            reserved = self.admission.reserve(
                self.session,
                self.queue_id,
                self.token,
                url,
                method=method,
                kind=kind,
                work_id=self.work_id,
            )
            self.session.commit()
            if not reserved["admitted"]:
                self.wait_until(reserved["retry_at"])
                continue
            self.latest_attempt = reserved["id"]
            self.admission.admit(self.session, self.latest_attempt, self.token)
            self.session.commit()
            return self.latest_attempt

    def response(self, status_code, retry_after=None):
        if self.latest_attempt is not None:
            self.admission.finish(
                self.session,
                self.latest_attempt,
                self.token,
                status_code=status_code,
                retry_after=retry_after,
            )
            self.session.commit()

    def persist(self, result, depth, asset_type):
        with atomic_collection(self.session):
            self.queues.check_lease(self.session, self.queue_id, self.token)
            before = set(
                self.session.scalars(
                    select(ScanEvidence.id).where(ScanEvidence.scan_run_id == self.run.id)
                )
            )
            asset = self.pipeline._persist_fetch(
                self.session,
                self.run,
                result,
                depth=depth,
                asset_type_hint=asset_type,
                context=self.context,
            )
            evidence = list(
                self.session.scalars(
                    select(ScanEvidence).where(
                        ScanEvidence.scan_run_id == self.run.id, ScanEvidence.id.not_in(before)
                    )
                )
            )
            size = sum(
                len(
                    json.dumps(
                        dict(
                            title=e.title, source_url=e.source_url, summary=e.summary, data=e.data
                        ),
                        ensure_ascii=False,
                    ).encode()
                )
                for e in evidence
            )
            self.admission.charge_evidence(self.session, self.latest_attempt, self.token, size)
            return asset

    def close(self):
        try:
            self.queues.close(self.session, self.queue_id, self.token)
            self.session.commit()
        except ConflictError:
            self.session.rollback()
            # A lost lease is left for expiration recovery; never overwrite its new owner.


class IdentityCollectionAdmission:
    """A verifier-thread adapter using its own Session and the collector's lease."""

    def __init__(self, runtime, session, admit):
        from copy import copy

        self.runtime = copy(runtime)
        self.runtime.session = session
        self.runtime.work_id = None
        self.admit = admit

    def __call__(self):
        self.admit()

    def for_request(self, url, method):
        self.admit()
        return self.runtime.before_send(url, method, kind="identity_health")

    def response(self, attempt, status_code, headers):
        self.runtime.admission.finish(
            self.runtime.session,
            attempt,
            self.runtime.token,
            status_code=status_code,
            retry_after=headers.get("retry-after"),
        )
        self.runtime.session.commit()
