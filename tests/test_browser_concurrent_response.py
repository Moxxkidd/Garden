"""Public scan regression for concurrent same-URL transport responses."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.core.settings import get_settings
from app.db.bootstrap import session_scope
from app.schemas.collection_queue import CollectionOptions
from app.schemas.scan import ScanOptions
from app.services.collection_queue import CollectionQueueService
from app.services.scan_application import InlineScanDispatcher, ScanApplicationService
from app.services.scan_network import HttpScanGateway, TargetNetworkPolicy
from app.services.scan_pipeline import ScanPipeline
from app.services.scan_reporting import ScanReportService
from tests.test_scan_preview_e2e import _serve


@pytest.mark.parametrize("success_first", [False, True])
def test_successful_concurrent_response_does_not_inherit_other_attempt_failure(
    tmp_path, success_first
):
    site = FastAPI()
    calls = []
    arrivals = []

    @site.get("/", response_class=HTMLResponse)
    def home():
        calls.append("/")
        return '<link rel="icon" href="data:,"><script>fetch("/api");fetch("/api");</script>'

    @site.get("/api")
    async def api():
        number = len(arrivals)
        arrivals.append(number)
        calls.append("/api")
        while len(arrivals) < 2:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05 if number == 0 else 0.2)
        status = 200 if (number == 0) == success_first else 503
        return HTMLResponse("<p>synthetic response</p>", status_code=status)

    @site.get("/later", response_class=HTMLResponse)
    def later():
        calls.append("/later")
        return "<p>later</p>"

    with _serve(site) as base:
        policy = TargetNetworkPolicy(get_settings())
        service = ScanApplicationService(
            dispatcher=InlineScanDispatcher(),
            pipeline=ScanPipeline(
                policy=policy,
                gateway=HttpScanGateway(policy),
                report_service=ScanReportService(tmp_path / "reports"),
            ),
        )
        try:
            first = service.start_scan(
                base,
                ScanOptions(
                    collection_mode="browser",
                    render_wait_ms=300,
                    max_pages=1,
                    durable=CollectionOptions(max_requests=3, min_interval_ms=0, retry_attempts=0),
                ),
            )
            with session_scope() as session:
                queues = CollectionQueueService()
                queue = queues.for_run(session, first.id)[0]
                assert queue["metrics"]["requests_used"] == 3
                assert queue["metrics"]["responses"] == 3
                assert queue["metrics"]["retry_requests"] == 0
                assert queue["counts"] == {"observed": 2}
                assert queue["coverage_complete"]
            if success_first:
                assert calls.count("/api") == 2
                return
            second = service.start_scan(
                base + "/later",
                ScanOptions(
                    overall_timeout_seconds=1,
                    durable=CollectionOptions(max_requests=1, min_interval_ms=0, retry_attempts=0),
                ),
            )
            with session_scope() as session:
                queue = CollectionQueueService().for_run(session, second.id)[0]
                assert queue["counts"] == {"observed": 1}
            assert calls.count("/api") == 2 and calls.count("/later") == 1
        finally:
            service.shutdown()


@pytest.mark.parametrize("body_kind", ["bounded", "unbounded", "large", "encoded"])
def test_durable_browser_only_uses_bounded_plain_body_for_asset_signals(tmp_path, body_kind):
    import gzip

    from fastapi import Response
    from fastapi.responses import StreamingResponse

    from app.schemas.assets import AssetQuery
    from app.services.asset_catalog import AssetCatalogService

    site = FastAPI()
    body = b'<link rel="icon" href="data:,"><title>Login</title><input type="password">'

    @site.get("/")
    def home():
        if body_kind == "unbounded":
            return StreamingResponse(iter([body]), media_type="text/html")
        if body_kind == "encoded":
            return Response(
                gzip.compress(body), media_type="text/html", headers={"Content-Encoding": "gzip"}
            )
        return Response(
            body + (b" " * (256 * 1024) if body_kind == "large" else b""), media_type="text/html"
        )

    with _serve(site) as base:
        policy = TargetNetworkPolicy(get_settings())
        service = ScanApplicationService(
            dispatcher=InlineScanDispatcher(),
            pipeline=ScanPipeline(
                policy=policy,
                gateway=HttpScanGateway(policy),
                report_service=ScanReportService(tmp_path / "reports"),
            ),
        )
        try:
            result = service.start_scan(
                base,
                ScanOptions(
                    collection_mode="browser",
                    max_pages=1,
                    render_wait_ms=0,
                    durable=CollectionOptions(max_requests=1, min_interval_ms=0),
                ),
            )
            with session_scope() as session:
                assets = (
                    AssetCatalogService()
                    .select(session, AssetQuery(source="scan", run_id=result.id))
                    .items
                )
                assert len(assets) == 1
                codes = {flag.code for flag in assets[0].validity.flags}
                assert ("login_page" in codes) == (body_kind == "bounded")
                queue = CollectionQueueService().for_run(session, result.id)[0]
                assert queue["metrics"]["requests_used"] == 1
                assert queue["counts"]["observed"] == 1
        finally:
            service.shutdown()
