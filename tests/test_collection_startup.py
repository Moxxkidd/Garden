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


def test_expired_source_can_resume_without_restarting_service_again(seeded_records, tmp_path):
    import time

    import httpx
    import pytest

    from app.core.errors import ConflictError
    from app.schemas.collection_resume import CollectionResumeRequest
    from app.services.collection_resume import CollectionResumeService

    now = [time.time()]
    queues = CollectionQueueService(clock=lambda: now[0])
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        run.status = "running"
        rid = run.id
        qid = queues.create(session, rid, run.contexts[0].id, CollectionOptions())["id"]
        queues.enqueue(session, qid, target.base_url.rstrip("/") + "/next")
        lease = queues.lease(session, qid)
        work = queues.claim(session, qid, lease)
    sent = []

    def respond(request):
        sent.append(request.url.path)
        return httpx.Response(200, text="fixture")

    service = _service(tmp_path, respond)
    try:
        assert service.interrupt_active_scans() == 0
        assert sent == []
        now[0] += 121
        resumes = CollectionResumeService(queues=queues, scans=service)
        request = CollectionResumeRequest(queue_id=qid)
        with session_scope() as session:
            preview = resumes.preview(session, request)
            assert service.get_scan(rid).status == "running"
            assert sent == []
            child = resumes.start(session, request, preview["token"])
        assert child != rid and sent == ["/next"]
        assert service.get_scan(rid).status == "interrupted"
        with session_scope() as session:
            with pytest.raises(ConflictError):
                queues.complete(session, qid, work["id"], lease)
            assert queues.get(session, qid)["counts"] == {"transferred": 1}
    finally:
        service.shutdown()
