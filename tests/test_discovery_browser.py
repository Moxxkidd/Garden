from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from app.core.errors import ScanInterrupted
from app.core.settings import Settings
from app.schemas.scan import ScanOptions
from app.services.discovery_browser import AnonymousDiscoveryBrowser
from app.services.scan_network import TargetNetworkPolicy


@contextmanager
def site():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.command, self.path))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/end")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b'{"ok":true}'
            kind = "application/json"
            if self.path == "/":
                kind = "text/html"
                body = b"""<a href="#/one">One</a><a href="#!/two">Two</a>
<a href="#section">Anchor</a><form method="post" action="/form"></form>
<script>
setTimeout(() => fetch('/delayed'), 20);
fetch('/mutate', {method:'POST'}).catch(()=>{});
fetch('http://localhost:1/out').catch(()=>{});
window.onhashchange = () => fetch('/view?route='+encodeURIComponent(location.hash));
</script>"""
            self.send_response(200)
            self.send_header("Content-Type", kind)
            if self.path != "/unbounded":
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            requests.append((self.command, self.path))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def collect(base, **overrides):
    observations, candidates = [], []
    options = ScanOptions().model_copy(
        update={
            "max_browser_requests": 40,
            "render_wait_ms": 100,
            "max_candidates": 200,
            **overrides,
        }
    )
    browser = AnonymousDiscoveryBrowser(TargetNetworkPolicy(Settings(allow_private_targets=True)))
    result = browser.collect(
        base,
        options,
        lambda: None,
        lambda response, route: observations.append((response, route)),
        candidates.append,
    )
    return result, observations, candidates


def test_real_browser_dynamic_hash_and_readonly_boundary():
    with site() as (base, requests):
        result, observations, candidates = collect(base + "/")
    assert ("GET", "/delayed") in requests
    assert not any(method == "POST" for method, _ in requests)
    assert any("/view?route=%23%2Fone" == path for _, path in requests)
    assert any("/view?route=%23!%2Ftwo" == path for _, path in requests)
    assert any(
        item["url"].endswith("/mutate") and item["method"] == "POST" and not item["auto_visit"]
        for item in candidates
    )
    assert any(item["source_kind"] == "hash_route" for item in candidates)
    assert not any(item["url"].endswith("#section") for item in candidates)
    assert any(response.body_text == '{"ok":true}' for response, _ in observations)
    assert result["request_attempts"] >= len(requests)
    assert result["request_attempts"] <= 40


def test_real_browser_request_budget_is_before_send_and_redirect_counts():
    with site() as (base, requests):
        result, _, _ = collect(base + "/redirect", max_browser_requests=1)
    assert requests == [("GET", "/redirect")]
    assert result == {"request_attempts": 1, "stopped_reason": "max_browser_requests"}


def test_real_browser_without_content_length_has_no_complete_body():
    with site() as (base, _):
        _, observations, _ = collect(base + "/unbounded")
    assert observations
    assert observations[0][0].body_truncated is True
    assert observations[0][0].body_text == ""


def test_real_browser_cancellation_propagates_and_stops_requests():
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls > 2:
            raise ScanInterrupted("cancel test")

    with site() as (base, requests):
        browser = AnonymousDiscoveryBrowser(
            TargetNetworkPolicy(Settings(allow_private_targets=True))
        )
        with pytest.raises(ScanInterrupted):
            browser.collect(base, ScanOptions(), cancel, lambda *args: None, lambda item: None)
        count = len(requests)
        assert count <= 1


def test_iframe_documents_are_stopped_by_page_budget_before_send():
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    from tests.test_scan_preview_e2e import _serve

    app = FastAPI()
    calls = []

    @app.get("/")
    def root():
        calls.append("/")
        return HTMLResponse('<iframe src="/frame"></iframe>')

    @app.get("/frame")
    def frame():
        calls.append("/frame")
        return HTMLResponse("frame")

    with _serve(app) as base:
        result, _, candidates = collect(base + "/", max_pages=1, max_resources=0)
    assert calls == ["/"]
    assert result["stopped_reason"] == "max_pages"
    assert any(c.get("skipped_reason") == "max_pages" for c in candidates)
