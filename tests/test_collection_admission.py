import pytest

from app.core.errors import ConflictError
from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.collection_queue import CollectionOptions
from app.services.collection_admission import CollectionAdmissionService
from app.services.collection_queue import CollectionQueueService
from app.services.collection_storage import CollectionStorage
from tests.helpers.project_assets import source_run


def test_origin_gate_counts_redirects_and_health_without_refunding_uncertain_sends(
    seeded_records, tmp_path
):
    now = [100.0]
    queues = CollectionQueueService(
        storage=CollectionStorage(tmp_path / "private"), clock=lambda: now[0]
    )
    gate = CollectionAdmissionService(queues=queues, clock=lambda: now[0])
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = queues.create(session, run.id, run.contexts[0].id, CollectionOptions(max_requests=2))[
            "id"
        ]
        token = queues.lease(session, qid)
        first = gate.reserve(session, qid, token, target.base_url, kind="health")
        gate.admit(session, first["id"], token)
        wait = gate.reserve(session, qid, token, target.base_url + "/redirect", kind="redirect")
        assert wait == {"admitted": False, "retry_at": 100.25}
        now[0] = 100.25
        second = gate.reserve(session, qid, token, target.base_url + "/redirect", kind="redirect")
        gate.admit(session, second["id"], token)
        gate.finish(session, second["id"], token, status_code=200)
        assert gate.summary(session, qid)["possibly_sent"] == 1
        now[0] = 101
        with pytest.raises(ConflictError, match="collection_request_budget"):
            gate.reserve(session, qid, token, target.base_url)
        assert gate.summary(session, qid)["requests_used"] == 2


def test_retry_after_is_persistent_and_never_shortened(seeded_records, tmp_path):
    now = [100.0]
    queues = CollectionQueueService(
        storage=CollectionStorage(tmp_path / "private"), clock=lambda: now[0]
    )
    gate = CollectionAdmissionService(queues=queues, clock=lambda: now[0])
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = queues.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
        token = queues.lease(session, qid)
        attempt = gate.reserve(session, qid, token, target.base_url)
        gate.admit(session, attempt["id"], token)
        gate.finish(session, attempt["id"], token, status_code=429, retry_after="3600")
    now[0] = 200
    with session_scope() as session:
        fresh = CollectionAdmissionService(queues=queues, clock=lambda: now[0])
        assert fresh.reserve(session, qid, token, target.base_url)["retry_at"] == 3700


def test_same_origin_and_identity_cannot_bypass_concurrency(seeded_records, tmp_path):
    now = [1.0]
    queues = CollectionQueueService(
        storage=CollectionStorage(tmp_path / "private"), clock=lambda: now[0]
    )
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        one = source_run(session, target)
        two = source_run(session, target)
        q1 = queues.create(session, one.id, one.contexts[0].id, CollectionOptions())["id"]
        q2 = queues.create(session, two.id, two.contexts[0].id, CollectionOptions())["id"]
        queues.lease(session, q1)
        with pytest.raises(ConflictError, match="collection_origin_busy"):
            queues.lease(session, q2)
