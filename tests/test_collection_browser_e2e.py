from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.db.bootstrap import session_scope
from app.schemas.collection_queue import CollectionOptions
from app.schemas.scan import ScanOptions
from app.services.collection_queue import CollectionQueueService
from tests.test_scan_preview_e2e import _serve
from tests.test_url_scan_pipeline import _service


def test_browser_network_requests_are_durably_counted(tmp_path):
    site = FastAPI()
    seen = []

    @site.middleware("http")
    async def count(request, call_next):
        seen.append(request.url.path)
        return await call_next(request)

    @site.get("/", response_class=HTMLResponse)
    def home():
        return (
            '<link rel="icon" href="data:,"><script>fetch("/api");</script><a href="/next">next</a>'
        )

    @site.get("/api")
    def api():
        return {"fixture": True}

    @site.get("/next", response_class=HTMLResponse)
    def next_page():
        return '<link rel="icon" href="data:,"><p>done</p>'

    with _serve(site) as base:
        service = _service(tmp_path, lambda request: None)
        try:
            result = service.start_scan(
                base,
                ScanOptions(
                    collection_mode="browser",
                    render_wait_ms=100,
                    durable=CollectionOptions(min_interval_ms=0),
                ),
            )
            with session_scope() as session:
                queue = CollectionQueueService().for_run(session, result.id)[0]
                assert queue["metrics"]["requests_used"] == len(seen)
                assert queue["counts"]["observed"] >= 3
                assert "/api" in seen and "/next" in seen
        finally:
            service.shutdown()


def test_browser_sitemap_discovers_and_visits_hidden_asset(tmp_path):
    from fastapi import Request, Response

    site = FastAPI()
    seen = []

    @site.get("/", response_class=HTMLResponse)
    def home():
        return '<link rel="icon" href="data:,"><p>home</p>'

    @site.get("/sitemap.xml")
    def sitemap(request: Request):
        return Response(
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{request.base_url}hidden</loc></url></urlset>",
            media_type="application/xml",
        )

    @site.get("/hidden", response_class=HTMLResponse)
    def hidden():
        seen.append("hidden")
        return '<link rel="icon" href="data:,"><p>hidden</p>'

    with _serve(site) as base:
        service = _service(tmp_path, lambda request: None)
        try:
            result = service.start_scan(
                base,
                ScanOptions(
                    collection_mode="browser",
                    sitemap_enabled=True,
                    render_wait_ms=100,
                    durable=CollectionOptions(min_interval_ms=0),
                ),
            )
            assert seen == ["hidden"]
            with session_scope() as session:
                queues = CollectionQueueService()
                queue = queues.for_run(session, result.id)[0]
                items = queues.list_work(session, queue["id"])["items"]
                assert any(i["url"].endswith("/hidden") and i["state"] == "observed" for i in items)
        finally:
            service.shutdown()


def test_browser_redirect_and_hash_navigation_complete_their_work(tmp_path):
    from fastapi.responses import RedirectResponse

    site = FastAPI()

    @site.get("/")
    def home():
        return RedirectResponse("/app", status_code=302)

    @site.get("/app", response_class=HTMLResponse)
    def app():
        return '<link rel="icon" href="data:,"><a href="/app#/view">view</a>'

    with _serve(site) as base:
        service = _service(tmp_path, lambda request: None)
        try:
            result = service.start_scan(
                base,
                ScanOptions(
                    collection_mode="browser",
                    render_wait_ms=100,
                    durable=CollectionOptions(min_interval_ms=0),
                ),
            )
            with session_scope() as session:
                queues = CollectionQueueService()
                queue = queues.for_run(session, result.id)[0]
                items = queues.list_work(session, queue["id"])["items"]
                assert queue["coverage_complete"], items
                assert any(i["url"].endswith("#/view") and i["state"] == "observed" for i in items)
        finally:
            service.shutdown()


def test_browser_budget_preserves_observations_and_uncovered_work(tmp_path):
    site = FastAPI()
    seen = []

    @site.get("/", response_class=HTMLResponse)
    def home():
        seen.append("home")
        return '<link rel="icon" href="data:,"><a href="/next">next</a>'

    @site.get("/next", response_class=HTMLResponse)
    def next_page():
        seen.append("next")
        return '<link rel="icon" href="data:,"><p>next</p>'

    with _serve(site) as base:
        service = _service(tmp_path, lambda request: None)
        try:
            result = service.start_scan(
                base,
                ScanOptions(
                    collection_mode="browser",
                    render_wait_ms=100,
                    durable=CollectionOptions(max_requests=1, min_interval_ms=0),
                ),
            )
            assert seen == ["home"]
            assert result.status == "completed_with_warnings"
            assert result.asset_count == 1
            with session_scope() as session:
                queue = CollectionQueueService().for_run(session, result.id)[0]
                assert queue["metrics"]["requests_used"] == 1
                assert not queue["coverage_complete"]
                assert queue["counts"]["observed"] == 1
        finally:
            service.shutdown()


def test_browser_retry_after_retries_the_durable_work(tmp_path):
    from fastapi import Response

    site = FastAPI()
    seen = []

    @site.get("/", response_class=HTMLResponse)
    def home():
        seen.append("request")
        if len(seen) == 1:
            return Response(
                '<link rel="icon" href="data:,">slow down',
                status_code=429,
                media_type="text/html",
                headers={"Retry-After": "0"},
            )
        return '<link rel="icon" href="data:,"><p>ready</p>'

    with _serve(site) as base:
        service = _service(tmp_path, lambda request: None)
        try:
            result = service.start_scan(
                base,
                ScanOptions(
                    collection_mode="browser",
                    render_wait_ms=100,
                    durable=CollectionOptions(min_interval_ms=0, retry_attempts=1),
                ),
            )
            assert len(seen) == 2
            with session_scope() as session:
                queue = CollectionQueueService().for_run(session, result.id)[0]
                assert queue["coverage_complete"]
                assert queue["metrics"]["requests_used"] == 2
        finally:
            service.shutdown()


def test_known_only_browser_recovery_blocks_new_subrequests_and_continues(tmp_path):
    from app.schemas.collection_resume import CollectionResumeRequest
    from app.services.collection_resume import CollectionResumeService

    site = FastAPI()
    seen = []

    @site.get("/{path:path}", response_class=HTMLResponse)
    def page(path: str):
        seen.append(path)
        body = (
            '<a href="/one">one</a><a href="/two">two</a>'
            if not path
            else '<script>fetch("/new")</script>'
        )
        return '<link rel="icon" href="data:,">' + body

    with _serve(site) as base:
        scans = _service(tmp_path, lambda request: None)
        try:
            original = scans.start_scan(
                base,
                ScanOptions(
                    collection_mode="browser",
                    render_wait_ms=100,
                    durable=CollectionOptions(max_requests=1, min_interval_ms=0),
                ),
            )
            resume = CollectionResumeService(scans=scans)
            with session_scope() as session:
                queue = resume.queues.for_run(session, original.id)[0]
                request = CollectionResumeRequest(
                    queue_id=queue["id"], options=CollectionOptions(min_interval_ms=0)
                )
                preview = resume.preview(session, request)
            seen.clear()
            with session_scope() as session:
                child_id = resume.start(session, request, preview["token"])
            assert set(seen) == {"one", "two"}
            with session_scope() as session:
                child = resume.queues.for_run(session, child_id)[0]
                assert child["coverage_complete"]
                assert child["metrics"]["requests_used"] == 2
        finally:
            scans.shutdown()


def test_browser_pause_keeps_inflight_response_and_stops_new_navigation(tmp_path):
    site = FastAPI()
    seen = []

    @site.get("/{path:path}", response_class=HTMLResponse)
    def home(path: str):
        seen.append(path)
        run = service.list_scans()[0]
        with session_scope() as session:
            queues = CollectionQueueService()
            queue = queues.for_run(session, run.id)[0]
            queues.pause(session, queue["id"])
        return '<link rel="icon" href="data:,"><a href="/next">next</a>'

    with _serve(site) as base:
        service = _service(tmp_path, lambda request: None)
        try:
            result = service.start_scan(
                base,
                ScanOptions(
                    collection_mode="browser",
                    render_wait_ms=100,
                    durable=CollectionOptions(min_interval_ms=0),
                ),
            )
            assert len(seen) == 1
            assert result.asset_count == 1
            assert result.status != "failed"
            with session_scope() as session:
                queue = CollectionQueueService().for_run(session, result.id)[0]
                assert queue["status"] == "paused"
                assert not queue["coverage_complete"]
        finally:
            service.shutdown()


def test_browser_transport_failure_retries_within_durable_budget(tmp_path):
    import socketserver
    import threading

    received = []

    class InvalidHTTP(socketserver.BaseRequestHandler):
        def handle(self):
            data = self.request.recv(4096)
            if data:
                received.append(data)
                self.request.sendall(b"not-an-http-response\r\n\r\n")

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), InvalidHTTP) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        service = _service(tmp_path, lambda request: None)
        try:
            result = service.start_scan(
                f"http://127.0.0.1:{server.server_address[1]}/",
                ScanOptions(
                    collection_mode="browser",
                    render_wait_ms=0,
                    durable=CollectionOptions(max_requests=2, retry_attempts=1, min_interval_ms=0),
                ),
            )
            with session_scope() as session:
                queues = CollectionQueueService()
                queue = queues.for_run(session, result.id)[0]
                assert queue["metrics"]["requests_used"] == 2
                assert queue["metrics"]["responses"] == 0
                assert len(received) == 2
                assert not queue["coverage_complete"]
                work = queues.list_work(session, queue["id"])["items"]
                assert len(work) == 1 and work[0]["state"] == "failed"
        finally:
            service.shutdown()
            server.shutdown()
            thread.join(timeout=5)
