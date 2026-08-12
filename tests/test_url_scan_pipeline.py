from __future__ import annotations

import time

import httpx
from fastapi.testclient import TestClient

import app.main as main_app
from app.core.errors import InputValidationError, TargetPolicyError
from app.core.settings import get_settings
from app.schemas.scan import ScanOptions
from app.services.scan_application import InlineScanDispatcher, ScanApplicationService
from app.services.scan_network import HttpScanGateway, TargetNetworkPolicy
from app.services.scan_pipeline import ScanPipeline
from app.services.scan_reporting import ScanReportService


def _loopback_resolver(host, port, **kwargs):
    return [(2, 1, 6, "", ("127.0.0.1", port))]


def _service(tmp_path, handler, *, clock=time.monotonic):
    settings = get_settings()
    policy = TargetNetworkPolicy(settings, resolver=_loopback_resolver)
    gateway = HttpScanGateway(
        policy,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _: None,
    )
    pipeline = ScanPipeline(
        policy=policy,
        gateway=gateway,
        report_service=ScanReportService(tmp_path / "reports"),
        clock=clock,
    )
    return ScanApplicationService(
        settings=settings,
        pipeline=pipeline,
        dispatcher=InlineScanDispatcher(),
    )


def test_network_policy_validates_url_and_blocks_link_local() -> None:
    settings = get_settings().model_copy(
        update={"allow_non_local_targets": True, "allow_private_targets": True}
    )
    metadata_policy = TargetNetworkPolicy(
        settings,
        resolver=lambda host, port, **kwargs: [(2, 1, 6, "", ("169.254.169.254", port))],
    )
    try:
        metadata_policy.preflight("http://metadata.invalid/latest")
    except TargetPolicyError as error:
        assert "blocked" in str(error)
    else:
        raise AssertionError("Expected link-local metadata address to be blocked.")

    local_policy = TargetNetworkPolicy(get_settings(), resolver=_loopback_resolver)
    try:
        local_policy.normalize_url("ftp://127.0.0.1/file")
    except InputValidationError as error:
        assert "http or https" in str(error)
    else:
        raise AssertionError("Expected invalid scheme to be rejected.")
    try:
        local_policy.normalize_url("http://user:secret@127.0.0.1/")
    except InputValidationError as error:
        assert "Credentials" in str(error)
    else:
        raise AssertionError("Expected embedded credentials to be rejected.")


def test_network_retry_is_single_and_only_for_transient_failure() -> None:
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            raise httpx.ConnectError("temporary disconnect", request=request)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<title>OK</title>")

    settings = get_settings()
    policy = TargetNetworkPolicy(settings, resolver=_loopback_resolver)
    gateway = HttpScanGateway(
        policy, transport=httpx.MockTransport(handler), sleeper=lambda _: None
    )
    result = gateway.fetch(
        "http://127.0.0.1/",
        ScanOptions(request_timeout_seconds=1, retry_attempts=1),
    )
    assert result.attempts == 2
    assert len(calls) == 2


def test_redirect_to_link_local_is_blocked_before_second_request() -> None:
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})

    settings = get_settings()
    policy = TargetNetworkPolicy(settings, resolver=_loopback_resolver)
    gateway = HttpScanGateway(policy, transport=httpx.MockTransport(handler))
    try:
        gateway.fetch(
            "http://127.0.0.1/",
            ScanOptions(request_timeout_seconds=1, retry_attempts=0),
        )
    except TargetPolicyError as error:
        assert "blocked" in str(error)
    else:
        raise AssertionError("Expected redirect to link-local metadata to be blocked.")
    assert len(calls) == 1


def test_complete_pipeline_persists_structured_data_and_report(tmp_path) -> None:
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html", "server": "fixture"},
                text='<html><title>Home</title><a href="/about">About</a></html>',
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "x-content-type-options": "nosniff"},
            text="<html><title>About</title><p>Fixture</p></html>",
        )

    service = _service(tmp_path, handler)
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(max_pages=5, retry_attempts=0))
    assert scan.status == "completed"
    assert scan.progress == 100
    assert scan.asset_count == 2
    assert scan.evidence_count == 2
    assert scan.finding_count >= 1
    assert [stage.name for stage in scan.stages] == [
        "validate",
        "establish_contexts",
        "collect",
        "normalize",
        "compare_coverage",
        "replay_authorization",
        "analyze",
        "report",
    ]
    assert [stage.status for stage in scan.stages] == [
        "completed",
        "completed",
        "completed",
        "completed",
        "skipped",
        "skipped",
        "completed",
        "completed",
    ]
    report = service.read_report(scan.id)
    for heading in [
        "执行摘要",
        "扫描范围",
        "发现的资产",
        "关键属性",
        "证据",
        "风险或关注项",
        "扫描覆盖范围",
        "失败或未覆盖部分",
        "生成时间",
    ]:
        assert f"## {heading}" in report
    assert "Home" in report
    assert "E" in report


def test_binary_response_is_described_without_polluting_markdown(tmp_path) -> None:
    binary_body = b"\x00\x01\x02binary\x7f\x80data"

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<a href="/favicon.ico">Icon</a>',
            )
        return httpx.Response(
            200,
            headers={"content-type": "image/x-icon"},
            content=binary_body,
        )

    service = _service(tmp_path, handler)
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(max_pages=2, retry_attempts=0))
    report = service.read_report(scan.id)
    assert "non-text response omitted" in report
    assert "content-type=image/x-icon" in report
    assert f"size={len(binary_body)} bytes" in report
    assert "\x00" not in report


def test_partial_page_failure_completes_with_warning_and_diagnostic_report(tmp_path) -> None:
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<a href="/broken">Broken</a>',
            )
        raise httpx.ConnectError("fixture connection dropped", request=request)

    service = _service(tmp_path, handler)
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(retry_attempts=0))
    assert scan.status == "completed_with_warnings"
    assert scan.failures[0].code == "network_retry_exhausted"
    report = service.read_report(scan.id)
    assert "fixture connection dropped" in report
    assert "completed_with_warnings" in report


def test_empty_success_response_has_explicit_single_asset_result(tmp_path) -> None:
    service = _service(tmp_path, lambda request: httpx.Response(204))
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(max_pages=1, retry_attempts=0))
    assert scan.status == "completed"
    assert scan.asset_count == 1
    assert scan.finding_count == 0
    report = service.read_report(scan.id)
    assert "未识别到风险或关注项" in report
    assert "HTTP 204" in report


def test_page_limit_is_reported_as_incomplete_coverage(tmp_path) -> None:
    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/one">One</a><a href="/two">Two</a>',
        )

    service = _service(tmp_path, handler)
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(max_pages=1, retry_attempts=0))
    assert scan.status == "completed_with_warnings"
    assert any(failure.code == "coverage_limit_reached" for failure in scan.failures)
    assert "coverage_limit_reached" in service.read_report(scan.id)


def test_active_duplicate_submission_returns_same_parent_task(tmp_path) -> None:
    class HoldingDispatcher:
        def __init__(self):
            self.ids = []

        def submit(self, scan_run_id, callback):
            self.ids.append(scan_run_id)

    settings = get_settings()
    policy = TargetNetworkPolicy(settings, resolver=_loopback_resolver)
    pipeline = ScanPipeline(
        policy=policy,
        gateway=HttpScanGateway(policy, transport=httpx.MockTransport(lambda request: None)),
        report_service=ScanReportService(tmp_path / "reports"),
    )
    dispatcher = HoldingDispatcher()
    service = ScanApplicationService(settings=settings, pipeline=pipeline, dispatcher=dispatcher)
    first = service.start_scan("http://127.0.0.1/")
    second = service.start_scan("http://127.0.0.1/")
    assert first.id == second.id
    assert dispatcher.ids == [first.id]


def test_overall_timeout_is_persisted_and_gets_failure_report(tmp_path) -> None:
    class AdvancingClock:
        def __init__(self):
            self.value = 0.0

        def __call__(self):
            self.value += 0.6
            return self.value

    service = _service(
        tmp_path,
        lambda request: httpx.Response(204),
        clock=AdvancingClock(),
    )
    scan = service.start_scan(
        "http://127.0.0.1/", ScanOptions(overall_timeout_seconds=1, retry_attempts=0)
    )
    assert scan.status == "failed"
    assert scan.error_code == "overall_timeout"
    assert scan.report_path is not None
    assert "overall_timeout" in service.read_report(scan.id)


def test_web_and_http_entry_run_end_to_end(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    url = "http://127.0.0.1:8765/"

    def handler(request):
        body = '<html><title>Local Fixture</title><a href="/asset">Asset</a></html>'
        if request.url.path == "/asset":
            body = "<html><title>Asset Detail</title><p>safe local data</p></html>"
        return httpx.Response(200, headers={"content-type": "text/html"}, text=body)

    service = _service(tmp_path, handler)
    monkeypatch.setattr(main_app, "ScanApplicationService", lambda settings: service)
    with TestClient(main_app.create_app()) as client:
        submitted = client.post("/scans", data={"url": url}, follow_redirects=False)
        assert submitted.status_code == 303
        scan_id = int(submitted.headers["location"].rsplit("/", 1)[-1])
        payload = client.get(f"/api/scans/{scan_id}").json()
        assert payload["status"] == "completed"
        detail = client.get(f"/scans/{scan_id}")
        report = client.get(f"/api/scans/{scan_id}/report")
        download = client.get(f"/api/scans/{scan_id}/report?download=true")
        api_submission = client.post("/api/scans", json={"url": url})
    assert detail.status_code == 200
    assert "Local Fixture" in detail.text
    assert report.status_code == 200
    assert "Asset Detail" in report.text
    assert download.status_code == 200
    assert api_submission.status_code == 202
    assert api_submission.json()["status"] == "completed"
    assert (tmp_path / "reports" / f"scan-{scan_id}.md").exists()


def test_http_entry_rejects_invalid_url_before_dispatch(monkeypatch) -> None:
    settings = get_settings()
    policy = TargetNetworkPolicy(settings, resolver=_loopback_resolver)
    service = ScanApplicationService(
        settings=settings,
        pipeline=ScanPipeline(
            policy=policy,
            gateway=HttpScanGateway(policy),
        ),
        dispatcher=InlineScanDispatcher(),
    )
    monkeypatch.setattr(main_app, "ScanApplicationService", lambda settings: service)
    with TestClient(main_app.create_app()) as client:
        response = client.post("/api/scans", json={"url": "file:///etc/passwd"})
    assert response.status_code == 400
    assert "http or https" in response.json()["detail"]
