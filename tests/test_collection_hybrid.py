"""Explicit hybrid policy through the scan service and durable budget view."""

import httpx
import pytest

from app.db.bootstrap import session_scope
from app.schemas.collection_queue import CollectionOptions
from app.schemas.scan import ScanOptions
from app.services.collection_queue import CollectionQueueService
from tests.test_url_scan_pipeline import _service


@pytest.mark.parametrize("budget,cap,expected_browser", [(10, 300, 3), (3, 300, 0), (10, 1, 1)])
def test_hybrid_hash_trigger_uses_one_budget_and_reports_browser_allowance(
    tmp_path, budget, cap, expected_browser
):
    from tests.test_discovery_browser import site

    with site() as (base, requests):
        service = _service(tmp_path, lambda request: httpx.Response(500))
        service.pipeline.gateway.transport = None
        try:
            run = service.start_scan(
                base + "/",
                ScanOptions(
                    collection_mode="hybrid",
                    discovery_strategy="diversity",
                    durable=CollectionOptions(max_requests=budget, min_interval_ms=0),
                    render_wait_ms=100,
                    max_browser_requests=cap,
                ),
            )
            with session_scope() as session:
                queue = CollectionQueueService().for_run(session, run.id)[0]
            assert run.status != "failed", run.error_message
            assert len(requests) == queue["metrics"]["requests_used"] <= budget
            summary = run.discovery_summary["hybrid"]
            assert summary["browser_reserved"] == expected_browser
            assert summary["trigger"] == "hash_route"
            assert summary["http_requests"] == 1
            assert summary["browser_requests"] <= expected_browser
            assert not any(method == "POST" for method, _ in requests)
            assert summary["browser_status"] == (
                "bounded" if expected_browser else "no_browser_budget"
            )
        finally:
            service.shutdown()


def test_static_hybrid_returns_reservation_to_http_without_launch(tmp_path, monkeypatch):
    from app.services.discovery_browser import AnonymousDiscoveryBrowser

    def no_browser(*args, **kwargs):
        raise AssertionError("static site must not launch a browser")

    monkeypatch.setattr(AnonymousDiscoveryBrowser, "collect", no_browser)
    sent = []

    def handle(request):
        sent.append(request.url.path)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="".join(f'<a href="/page-{i}">page</a>' for i in range(20))
            if request.url.path == "/"
            else "ok",
        )

    service = _service(tmp_path, handle)
    try:
        run = service.start_scan(
            "http://localhost:8123/",
            ScanOptions(
                collection_mode="hybrid",
                durable=CollectionOptions(max_requests=10, min_interval_ms=0),
            ),
        )
        assert len(sent) == 10
        assert run.discovery_summary["hybrid"]["browser_requests"] == 0
        assert run.discovery_summary["hybrid"]["http_requests"] == 10
        assert run.discovery_summary["hybrid"]["browser_status"] == "not_triggered"
    finally:
        service.shutdown()


def test_launch_failure_preserves_http_results_and_explicit_gap(tmp_path, monkeypatch, app):
    from pathlib import Path

    from app.services.discovery_browser import AnonymousDiscoveryBrowser

    def unavailable(*args, **kwargs):
        from app.services.browser_discovery import BrowserUnavailableError

        raise BrowserUnavailableError(
            "Chromium could not start. Install it with: python -m playwright install chromium"
        )

    monkeypatch.setattr(AnonymousDiscoveryBrowser, "collect", unavailable)
    service = _service(
        tmp_path,
        lambda r: httpx.Response(
            200, headers={"content-type": "text/html"}, text='<a href="#/screen">route</a>'
        ),
    )
    try:
        run = service.start_scan(
            "http://localhost:8123/",
            ScanOptions(
                preset="deep",
                sitemap_enabled=False,
                js_enabled=False,
                durable=CollectionOptions(max_requests=10, min_interval_ms=0),
            ),
        )
        assert run.asset_count == 1
        assert run.status != "failed"
        assert run.discovery_summary["hybrid"]["browser_status"] == "unavailable"
        assert not run.discovery_summary["complete"]
        from html import unescape

        from app.models.scan_run import ScanRun
        from app.services.scan_result_presentation import discovery_explanation

        with session_scope() as session:
            options = session.get(ScanRun, run.id).options
            assert options["collection_mode"] == "hybrid"
            assert options["discovery_strategy"] == "diversity"
            assert options["sitemap_enabled"] is options["js_enabled"] is False
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            previous = app.state.scan_service
            app.state.scan_service = service
            try:
                page = unescape(client.get(f"/scans/{run.id}").text)
                report = Path(run.report_path).read_text()
                for line in discovery_explanation(run.discovery_summary):
                    assert line in page
                    assert line in report
            finally:
                app.state.scan_service = previous
    finally:
        service.shutdown()


def test_http_deadline_prevents_browser_start(tmp_path, monkeypatch):
    from app.services.discovery_browser import AnonymousDiscoveryBrowser

    clock = [0.0]

    def no_browser(*args, **kwargs):
        raise AssertionError("browser must not start after the shared deadline")

    monkeypatch.setattr(AnonymousDiscoveryBrowser, "collect", no_browser)

    def handle(request):
        clock[0] = 2.0
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text='<a href="#/view">route</a>'
        )

    service = _service(tmp_path, handle, clock=lambda: clock[0])
    try:
        run = service.start_scan(
            "http://localhost:8123/",
            ScanOptions(
                collection_mode="hybrid",
                overall_timeout_seconds=1,
                durable=CollectionOptions(max_requests=10, min_interval_ms=0),
            ),
        )
        assert run.status != "failed"
        assert run.asset_count == 1
        assert not run.discovery_summary["complete"]
        assert run.discovery_summary["hybrid"]["browser_requests"] == 0
    finally:
        service.shutdown()


@pytest.mark.parametrize("with_link,expected", [(False, "script_only_entry"), (True, None)])
def test_script_resource_triggers_only_without_ordinary_entry_links(
    tmp_path, monkeypatch, with_link, expected
):
    from app.services.browser_discovery import BrowserUnavailableError
    from app.services.discovery_browser import AnonymousDiscoveryBrowser

    launches = []

    def unavailable(*args, **kwargs):
        launches.append(True)
        raise BrowserUnavailableError("synthetic unavailable Chromium")

    monkeypatch.setattr(AnonymousDiscoveryBrowser, "collect", unavailable)

    def handle(request):
        body = '<script src="/app.js"></script>' + ('<a href="/page">page</a>' if with_link else "")
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=body if request.url.path == "/" else "ok",
        )

    service = _service(tmp_path, handle)
    try:
        run = service.start_scan(
            "http://localhost:8123/",
            ScanOptions(
                collection_mode="hybrid",
                durable=CollectionOptions(max_requests=10, min_interval_ms=0),
            ),
        )
        assert run.discovery_summary["hybrid"]["trigger"] == expected
        assert bool(launches) == (not with_link)
    finally:
        service.shutdown()


def test_hybrid_retry_and_redirect_share_the_recorded_total(tmp_path, monkeypatch):
    from app.services.browser_discovery import BrowserUnavailableError
    from app.services.discovery_browser import AnonymousDiscoveryBrowser

    def unavailable(*args, **kwargs):
        raise BrowserUnavailableError("synthetic Chromium unavailable")

    monkeypatch.setattr(AnonymousDiscoveryBrowser, "collect", unavailable)
    sent = []

    def handle(request):
        path = request.url.path
        sent.append(path)
        if path == "/" and sent.count("/") == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        if path == "/jump":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="#/view">route</a><a href="/jump">jump</a>' if path == "/" else "ok",
        )

    service = _service(tmp_path, handle)
    try:
        run = service.start_scan(
            "http://localhost:8123/",
            ScanOptions(
                collection_mode="hybrid",
                durable=CollectionOptions(max_requests=10, min_interval_ms=0, retry_attempts=1),
            ),
        )
        with session_scope() as session:
            queue = CollectionQueueService().for_run(session, run.id)[0]
        assert sent == ["/", "/", "/jump", "/final"]
        assert (
            queue["metrics"]["requests_used"]
            == run.discovery_summary["hybrid"]["http_requests"]
            == 4
        )
        assert queue["metrics"]["retry_requests"] == 1
        assert run.discovery_summary["hybrid"]["browser_status"] == "unavailable"
    finally:
        service.shutdown()
