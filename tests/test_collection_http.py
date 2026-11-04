import httpx
import pytest

from app.db.bootstrap import session_scope
from app.schemas.collection_queue import CollectionOptions
from app.schemas.scan import ScanOptions
from app.services.collection_queue import CollectionQueueService
from tests.test_url_scan_pipeline import _service


def test_durable_http_uses_real_scan_entry_and_retains_uncovered_work(tmp_path):
    sent = []

    def handle(request):
        sent.append(request.url.path)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/one">one</a><a href="/two">two</a>',
        )

    service = _service(tmp_path, handle)
    try:
        result = service.start_scan(
            "http://localhost:8123/",
            ScanOptions(durable=CollectionOptions(max_requests=2, min_interval_ms=0), max_depth=2),
        )
        assert len(sent) == 2
        with session_scope() as session:
            queues = CollectionQueueService().for_run(session, result.id)
            assert len(queues) == 1
            assert queues[0]["counts"]["observed"] == 2
            assert not queues[0]["coverage_complete"]
            assert queues[0]["metrics"]["requests_used"] == 2
        assert result.asset_count == 2
        assert result.status != "failed"
    finally:
        service.shutdown()


def test_durable_sitemaps_and_redirects_are_counted_and_retained(tmp_path):
    sent = []

    def handle(request):
        sent.append(request.url.path)
        if request.url.path == "/sitemap.xml":
            return httpx.Response(
                200,
                headers={"content-type": "application/xml"},
                text='<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>http://localhost:8123/hidden</loc></url></urlset>',
            )
        if request.url.path == "/":
            return httpx.Response(302, headers={"location": "/home"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<title>ok</title>")

    service = _service(tmp_path, handle)
    try:
        result = service.start_scan(
            "http://localhost:8123/",
            ScanOptions(durable=CollectionOptions(min_interval_ms=0), sitemap_enabled=True),
        )
        with session_scope() as session:
            queue = CollectionQueueService().for_run(session, result.id)[0]
            items = CollectionQueueService().list_work(session, queue["id"])["items"]
            assert any(
                x["url"].endswith("/sitemap.xml") and x["state"] == "observed" for x in items
            )
            assert queue["metrics"]["requests_used"] == len(sent) == 4
            assert queue["coverage_complete"]
        # Enabled Sitemap candidates become observations only after the bounded request.
        assert "/hidden" in sent
    finally:
        service.shutdown()


def test_expired_worker_cannot_finalize_or_publish_a_report(tmp_path):
    import time

    def handle(request):
        with session_scope() as session:
            CollectionQueueService(clock=lambda: time.time() + 121).recover_expired(session)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>late</p>")

    service = _service(tmp_path, handle)
    try:
        result = service.start_scan(
            "http://localhost:8123/",
            ScanOptions(
                durable=CollectionOptions(min_interval_ms=0),
            ),
        )
        assert result.status == "running"
        assert result.report_path is None
        assert result.asset_count == 0
        with session_scope() as session:
            queue = CollectionQueueService().for_run(session, result.id)[0]
            assert queue["status"] == "paused"
            assert queue["counts"] == {"uncertain": 1}
        assert service.interrupt_active_scans() == 1
        assert service.get_scan(result.id).status == "interrupted"
    finally:
        service.shutdown()


@pytest.mark.parametrize("concurrency", [1, 2, 4])
def test_http_workers_overlap_with_exact_admission_budget(tmp_path, concurrency):
    import threading
    import time

    active = maximum = 0
    sent = []
    lock = threading.Lock()

    def handle(request):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            sent.append(request.url.path)
        time.sleep(0.12)
        with lock:
            active -= 1
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="".join(f'<a href="/{i}">{i}</a>' for i in range(12))
            if request.url.path == "/"
            else "<p>fixture</p>",
        )

    service = _service(tmp_path, handle)
    try:
        result = service.start_scan(
            "http://localhost:8123/",
            ScanOptions(
                durable=CollectionOptions(
                    max_requests=7,
                    min_interval_ms=0,
                    origin_concurrency=concurrency,
                    retry_attempts=0,
                ),
                max_pages=20,
            ),
        )
        assert maximum == concurrency
        assert len(sent) == 7
        with session_scope() as session:
            queue = CollectionQueueService().for_run(session, result.id)[0]
            assert queue["metrics"]["requests_used"] == queue["metrics"]["responses"] == 7
            assert queue["counts"]["observed"] == 7
    finally:
        service.shutdown()


def test_pause_keeps_inflight_observations_without_new_admissions(tmp_path):
    from sqlalchemy import func, select

    from app.models.collection_queue import CollectionAttempt

    admitted_at_pause = []

    def handle(request):
        if request.url.path != "/":
            with session_scope() as session:
                q = CollectionQueueService().for_run(session, 1)[0]
                if q["status"] == "running":
                    CollectionQueueService().pause(session, q["id"])
                    admitted_at_pause.append(session.scalar(select(func.max(CollectionAttempt.id))))
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="".join(f'<a href="/{i}">page</a>' for i in range(12))
            if request.url.path == "/"
            else "observed",
        )

    service = _service(tmp_path, handle)
    try:
        result = service.start_scan(
            "http://localhost:8123/",
            ScanOptions(durable=CollectionOptions(origin_concurrency=4, min_interval_ms=0)),
        )
        with session_scope() as session:
            q = CollectionQueueService().for_run(session, result.id)[0]
            assert q["status"] == "paused"
            assert q["counts"]["observed"] >= 2
            assert session.scalar(select(func.max(CollectionAttempt.id))) == admitted_at_pause[0]
    finally:
        service.shutdown()
