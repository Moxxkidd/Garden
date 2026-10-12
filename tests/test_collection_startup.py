from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.collection_queue import CollectionOptions
from app.services.collection_queue import CollectionQueueService
from tests.helpers.project_assets import source_run
from tests.test_url_scan_pipeline import _service


def test_startup_does_not_interrupt_another_live_collection_worker(seeded_records, tmp_path):
    queues = CollectionQueueService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        run.status = "running"
        qid = queues.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
        queues.enqueue(session, qid, target.base_url)
        lease = queues.lease(session, qid)
    service = _service(tmp_path, lambda request: None)
    try:
        assert service.interrupt_active_scans() == 0
        with session_scope() as session:
            assert queues.claim(session, qid, lease) is not None
    finally:
        service.shutdown()
