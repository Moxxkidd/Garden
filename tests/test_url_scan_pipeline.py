from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main as main_app
from app.core.errors import InputValidationError, TargetPolicyError
from app.core.settings import get_settings
from app.db.bootstrap import session_scope
from app.models.scan_run import ScanAsset, ScanRun
from app.schemas.scan import ScanOptions
from app.services.scan_application import InlineScanDispatcher, ScanApplicationService
from app.services.scan_failure_classification import is_coverage_warning
from app.services.scan_network import MAX_RESPONSE_BYTES, HttpScanGateway, TargetNetworkPolicy
from app.services.scan_pipeline import ScanPipeline
from app.services.scan_reporting import ScanReportService


def test_only_collect_boundary_and_budget_codes_are_coverage_warnings() -> None:
    assert is_coverage_warning("collect", "coverage_limit_reached")
    assert is_coverage_warning("collect", "cross_origin_redirect_blocked")
    assert is_coverage_warning("collect", "overall_timeout")
    assert not is_coverage_warning("validate", "cross_origin_redirect_blocked")
    assert not is_coverage_warning("collect", "connect_error")
    assert not is_coverage_warning("report", "stage_execution_failed")


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


@pytest.mark.parametrize("scenario", ["redirect", "retry"])
def test_network_guard_runs_before_redirect_and_retry_requests(scenario) -> None:
    class GuardStopped(RuntimeError):
        pass

    requests: list[str] = []
    guard_checks = 0

    def handler(request):
        requests.append(str(request.url))
        if scenario == "redirect":
            return httpx.Response(302, headers={"location": "/next"})
        raise httpx.ConnectError("retry me", request=request)

    def guard() -> None:
        nonlocal guard_checks
        guard_checks += 1
        if guard_checks == 2:
            raise GuardStopped

    policy = TargetNetworkPolicy(get_settings(), resolver=_loopback_resolver)
    gateway = HttpScanGateway(
        policy,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _: None,
    )

    with pytest.raises(GuardStopped):
        gateway.fetch(
            "http://127.0.0.1/",
            ScanOptions(max_redirects=1, retry_attempts=1),
            before_request=guard,
        )

    assert guard_checks == 2
    assert requests == ["http://127.0.0.1/"]


def test_scan_defaults_expand_page_and_resource_budgets() -> None:
    options = ScanOptions()

    assert options.max_pages == 50
    assert options.max_resources == 200


def test_discovery_classifies_resources_and_normalizes_duplicate_headers() -> None:
    def handler(request):
        return httpx.Response(
            200,
            headers=[
                ("content-type", "text/html"),
                ("frame-options", "SAMEORIGIN"),
                ("x-frame-options", "SAMEORIGIN, SAMEORIGIN"),
            ],
            text=(
                '<link rel="stylesheet" href="/app.css">'
                '<script src="/app.js"></script>'
                '<img src="/logo.png">'
                '<a href="/guide.pdf">Guide</a>'
                '<a href="/about">About</a>'
            ),
        )

    settings = get_settings()
    policy = TargetNetworkPolicy(settings, resolver=_loopback_resolver)
    result = HttpScanGateway(policy, transport=httpx.MockTransport(handler)).fetch(
        "http://127.0.0.1/", ScanOptions(retry_attempts=0)
    )

    assert [(item.asset_type, item.url) for item in result.discovered_assets] == [
        ("stylesheet", "http://127.0.0.1/app.css"),
        ("script", "http://127.0.0.1/app.js"),
        ("image", "http://127.0.0.1/logo.png"),
        ("document", "http://127.0.0.1/guide.pdf"),
        ("page", "http://127.0.0.1/about"),
    ]
    assert "frame-options" not in result.headers
    assert result.headers["x-frame-options"] == "SAMEORIGIN"


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


def test_entry_connection_error_remains_a_fatal_request_failure(tmp_path) -> None:
    def handler(request):
        raise httpx.ConnectError("fixture connect failure", request=request)

    service = _service(tmp_path, handler)
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(retry_attempts=0))

    assert scan.status == "failed"
    assert scan.error_code == "network_retry_exhausted"
    context = scan.contexts[0]
    assert context.status == "failed"
    assert context.collection_status == "failed"
    assert context.completeness == "incomplete"
    assert context.failure_count == 1
    assert context.error_code == "network_retry_exhausted"
    assert context.error_message == scan.error_message
    report = service.read_report(scan.id)
    assert "- 覆盖告警：0" in report
    assert "- 请求或阶段失败：1" in report
    assert "network_retry_exhausted" in report.split("## 请求失败", 1)[1]


@pytest.mark.parametrize(
    ("with_coverage_warning", "expected_collection_status", "expected_completeness"),
    [
        (False, "completed", "legacy_single_context"),
        (True, "completed_with_warnings", "incomplete"),
    ],
)
def test_later_stage_failure_preserves_finalized_collection_state(
    tmp_path,
    with_coverage_warning,
    expected_collection_status,
    expected_completeness,
) -> None:
    class FailingAnalyzer:
        def analyze(self, assets, evidence):
            raise RuntimeError("fixture analyze failure")

    def handler(request):
        if request.url.path == "/":
            body = '<a href="/redirect">Redirect</a>' if with_coverage_warning else ""
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=body,
            )
        return httpx.Response(302, headers={"location": "http://other.example/final"})

    service = _service(tmp_path, handler)
    service.pipeline.analyzer = FailingAnalyzer()

    scan = service.start_scan("http://127.0.0.1/", ScanOptions(retry_attempts=0))

    assert scan.status == "failed"
    assert scan.error_code == "stage_execution_failed"
    collect = next(stage for stage in scan.stages if stage.name == "collect")
    assert collect.status == expected_collection_status
    context = scan.contexts[0]
    assert context.status == "failed"
    assert context.collection_status == expected_collection_status
    assert context.completeness == expected_completeness
    assert context.failure_count == 1
    assert context.error_code == "stage_execution_failed"
    assert context.error_message == "fixture analyze failure"


def test_empty_success_response_has_explicit_single_asset_result(tmp_path) -> None:
    service = _service(tmp_path, lambda request: httpx.Response(204))
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(max_pages=1, retry_attempts=0))
    assert scan.status == "completed"
    assert scan.asset_count == 1
    assert scan.finding_count == 0
    report = service.read_report(scan.id)
    assert "未识别到风险或关注项" in report
    assert "HTTP 204" in report


def test_pages_are_prioritized_and_coverage_limit_is_a_structured_warning(tmp_path) -> None:
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<link rel="stylesheet" href="/app.css">'
                    '<script src="/app.js"></script>'
                    '<img src="/logo.png">'
                    '<a href="/about">About</a>'
                    '<a href="/news">News</a>'
                    '<a href="/contact">Contact</a>'
                ),
            )
        content_types = {
            "/app.css": "text/css",
            "/app.js": "application/javascript",
            "/logo.png": "image/png",
        }
        return httpx.Response(
            200,
            headers={"content-type": content_types.get(request.url.path, "text/html")},
            text="<html><title>Fixture</title></html>",
        )

    service = _service(tmp_path, handler)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(max_pages=2, max_resources=2, retry_attempts=0),
    )

    assert scan.status == "completed_with_warnings"
    assert any(failure.code == "coverage_limit_reached" for failure in scan.failures)
    assert calls == ["/", "/about", "/app.css", "/app.js"]
    with session_scope() as session:
        asset_types = list(
            session.scalars(
                select(ScanAsset.asset_type)
                .where(ScanAsset.scan_run_id == scan.id)
                .order_by(ScanAsset.id)
            )
        )
    assert asset_types == ["page", "page", "stylesheet", "script"]

    report = service.read_report(scan.id)
    assert "## 覆盖告警" in report
    assert "候选 URL 总数：7" in report
    assert "已请求数量：4" in report
    assert "未覆盖数量：3" in report
    assert "命中限制：max_pages=2, max_resources=2" in report
    assert "page：2" in report
    assert "image：1" in report
    assert "http://127.0.0.1/news" in report
    assert "http://127.0.0.1/logo.png" in report
    failure_section = report.split("## 请求失败", 1)[1]
    assert "coverage_limit_reached" not in failure_section


def test_static_resources_use_metadata_summary_and_report_positive_controls(tmp_path) -> None:
    stylesheet = "/* Library 1.2.3 */ @import url('/theme.css'); .secret-marker { color: red; }"
    javascript = (
        "/*! Widget v4.5.6 */ document.write('secret-long-preview-marker'); "
        "//# sourceMappingURL=widget.js.map"
    )

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={
                    "content-type": "text/html",
                    "strict-transport-security": "max-age=15768000",
                    "x-frame-options": "SAMEORIGIN",
                },
                text=(
                    '<link rel="stylesheet" href="/library-1.2.3.css">'
                    '<script src="/widget-4.5.6.js"></script>'
                ),
            )
        if request.url.path.endswith(".css"):
            return httpx.Response(200, headers={"content-type": "text/css"}, text=stylesheet)
        return httpx.Response(
            200,
            headers={"content-type": "application/javascript"},
            text=javascript,
        )

    service = _service(tmp_path, handler)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(max_pages=1, max_resources=2, retry_attempts=0),
    )
    report = service.read_report(scan.id)

    assert "| stylesheet |" in report
    assert "| script |" in report
    assert "## 已观察到的安全控制" in report
    assert "Strict-Transport-Security=max-age=15768000" in report
    assert "X-Frame-Options=SAMEORIGIN" in report
    assert "资源摘要：size=" in report
    assert "SHA-256=" in report
    assert "版本线索=1.2.3" in report
    assert "版本线索=4.5.6" in report
    assert "安全信号=external_import" in report
    assert "source_map_reference" in report
    assert "secret-long-preview-marker" not in report
    assert ".secret-marker" not in report
    assert "### 静态资源证据索引" in report
    assert report.count("### E") == 1


def test_collection_blocks_cross_origin_redirect_before_following_it(tmp_path) -> None:
    calls = []

    def handler(request):
        calls.append((request.url.host, request.url.path))
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<a href="/redirect">Redirect</a>',
            )
        return httpx.Response(
            302,
            headers={"location": "http://other.example/final"},
        )

    service = _service(tmp_path, handler)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(max_pages=2, retry_attempts=0),
    )

    assert calls == [("127.0.0.1", "/"), ("127.0.0.1", "/redirect")]
    assert scan.status == "completed_with_warnings"
    assert any(failure.code == "cross_origin_redirect_blocked" for failure in scan.failures)
    assert scan.error_code is None
    assert scan.contexts[0].collection_status == "completed_with_warnings"
    assert scan.contexts[0].completeness == "incomplete"
    assert scan.contexts[0].failure_count == 0
    report = service.read_report(scan.id)
    assert "- 覆盖告警：1" in report
    assert "- 请求或阶段失败：0" in report
    coverage = report.split("## 覆盖告警", 1)[1].split("## 请求失败", 1)[0]
    request_failures = report.split("## 请求失败", 1)[1]
    assert "cross_origin_redirect_blocked" in coverage
    assert "cross_origin_redirect_blocked" not in request_failures


def test_truncated_resource_is_labeled_as_a_collected_fragment(tmp_path) -> None:
    oversized = b"x" * (MAX_RESPONSE_BYTES + 32)

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<script src="/large.js"></script>',
            )
        return httpx.Response(
            200,
            headers={"content-type": "application/javascript"},
            content=oversized,
        )

    service = _service(tmp_path, handler)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(max_pages=1, max_resources=1, retry_attempts=0),
    )
    report = service.read_report(scan.id)

    assert "truncated=true" in report
    assert "采集片段 SHA-256=" in report
    assert f"size={MAX_RESPONSE_BYTES} bytes" in report


def test_version_hints_require_context_and_include_version_query(tmp_path) -> None:
    stylesheet = "path { d: 17.405 20.651 194.423; opacity: 0.18; }"

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<link rel="stylesheet" href="/style.css?v=1.0.0">',
            )
        return httpx.Response(200, headers={"content-type": "text/css"}, text=stylesheet)

    service = _service(tmp_path, handler)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(max_pages=1, max_resources=1, retry_attempts=0),
    )
    report = service.read_report(scan.id)

    assert "版本线索=1.0.0" in report
    assert "17.405" not in report
    assert "20.651" not in report
    assert "0.18" not in report


def test_report_uses_request_buckets_and_aggregates_repeated_findings(tmp_path) -> None:
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<a href="/about">About</a><script src="/html-script.js"></script>',
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><title>Fixture</title></html>",
        )

    service = _service(tmp_path, handler)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(max_pages=2, max_resources=1, retry_attempts=0),
    )
    report = service.read_report(scan.id)

    assert "页面队列 2/2，资源队列 1/1" in report
    assert report.count("缺少 Content-Security-Policy 响应头") == 1
    assert report.count("缺少 X-Content-Type-Options 响应头") == 1
    assert "3 个资产" in report
    assert "2 类（6 条原始观察）" in report


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


def test_overall_timeout_preserves_partial_collection_and_finishes_report(tmp_path) -> None:
    class ControlledClock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += seconds

    clock = ControlledClock()
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<a href="/first">First</a><a href="/never">Never</a>',
            )
        if request.url.path == "/first":
            clock.advance(2.0)
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<title>First</title>",
            )
        raise AssertionError("No request may start after the deadline")

    service = _service(tmp_path, handler, clock=clock)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(max_pages=3, overall_timeout_seconds=1, retry_attempts=0),
    )

    assert calls == ["/", "/first"]
    assert scan.status == "completed_with_warnings"
    assert scan.error_code is None
    assert scan.asset_count == 2
    assert scan.evidence_count == 2
    selected = [
        stage.status
        for stage in scan.stages
        if stage.name in {"collect", "normalize", "analyze", "report"}
    ]
    assert selected == ["completed_with_warnings", "completed", "completed", "completed"]
    assert [failure.code for failure in scan.failures].count("overall_timeout") == 1
    assert scan.contexts[0].collection_status == "completed_with_warnings"
    assert scan.contexts[0].completeness == "incomplete"
    assert scan.contexts[0].failure_count == 0
    report = service.read_report(scan.id)
    assert "First" in report
    assert "- 覆盖告警：1" in report
    assert "- 请求或阶段失败：0" in report
    coverage = report.split("## 覆盖告警", 1)[1].split("## 请求失败", 1)[0]
    request_failures = report.split("## 请求失败", 1)[1]
    assert "overall_timeout" in coverage
    assert "overall_timeout" not in request_failures


def test_deadline_equality_blocks_the_next_target_request(tmp_path) -> None:
    class ControlledClock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = ControlledClock()
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/":
            clock.value = 1.0
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<a href="/must-not-run">Blocked</a>',
            )
        raise AssertionError("No target request may start at the deadline")

    service = _service(tmp_path, handler, clock=clock)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(max_pages=2, overall_timeout_seconds=1, retry_attempts=0),
    )

    assert calls == ["/"]
    assert scan.status == "completed_with_warnings"
    assert [failure.code for failure in scan.failures].count("overall_timeout") == 1


@pytest.mark.parametrize("scenario", ["redirect", "retry"])
def test_entry_request_timeout_is_a_graceful_coverage_warning(tmp_path, scenario) -> None:
    class ControlledClock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = ControlledClock()
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        clock.value = 1.0
        if scenario == "redirect":
            return httpx.Response(302, headers={"location": "/never"})
        raise httpx.ConnectError("retry after deadline", request=request)

    service = _service(tmp_path, handler, clock=clock)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(
            overall_timeout_seconds=1,
            retry_attempts=1,
            max_redirects=1,
        ),
    )

    assert calls == ["/"]
    assert scan.status == "completed_with_warnings"
    assert scan.error_code is None
    assert scan.asset_count == 0
    assert scan.evidence_count == 0
    assert [failure.code for failure in scan.failures].count("overall_timeout") == 1
    collect = next(stage for stage in scan.stages if stage.name == "collect")
    assert collect.status == "completed_with_warnings"
    for stage_name in ("normalize", "analyze", "report"):
        stage = next(stage for stage in scan.stages if stage.name == stage_name)
        assert stage.status == "completed"


def test_two_cross_origin_redirects_and_timeout_are_coverage_warnings(tmp_path) -> None:
    class ControlledClock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += seconds

    clock = ControlledClock()
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<a href="/redirect-one">One</a><a href="/redirect-two">Two</a>',
            )
        if request.url.path == "/redirect-two":
            clock.advance(2.0)
        return httpx.Response(302, headers={"location": "http://other.example/final"})

    service = _service(tmp_path, handler, clock=clock)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(max_pages=3, overall_timeout_seconds=1, retry_attempts=0),
    )

    assert calls == ["/", "/redirect-one", "/redirect-two"]
    assert scan.status == "completed_with_warnings"
    assert scan.error_code is None
    assert scan.contexts[0].collection_status == "completed_with_warnings"
    assert scan.contexts[0].completeness == "incomplete"
    assert scan.contexts[0].failure_count == 0
    report = service.read_report(scan.id)
    assert "- 覆盖告警：3" in report
    assert "- 请求或阶段失败：0" in report
    coverage = report.split("## 覆盖告警", 1)[1].split("## 请求失败", 1)[0]
    request_failures = report.split("## 请求失败", 1)[1]
    assert coverage.count("cross_origin_redirect_blocked") == 2
    assert "overall_timeout" in coverage
    assert "cross_origin_redirect_blocked" not in request_failures
    assert "overall_timeout" not in request_failures


def test_overall_timeout_after_last_child_preserves_response_and_records_warning(tmp_path) -> None:
    class ControlledClock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += seconds

    clock = ControlledClock()
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<a href="/first">First</a>',
            )
        if request.url.path == "/first":
            clock.advance(2.0)
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<title>First</title>",
            )
        raise AssertionError("Unexpected target request")

    service = _service(tmp_path, handler, clock=clock)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(max_pages=2, overall_timeout_seconds=1, retry_attempts=0),
    )

    assert calls == ["/", "/first"]
    assert scan.status == "completed_with_warnings"
    assert scan.asset_count == 2
    assert scan.evidence_count == 2
    assert [failure.code for failure in scan.failures].count("overall_timeout") == 1
    assert "First" in service.read_report(scan.id)


@pytest.mark.parametrize(
    ("entry_html", "options"),
    [
        (
            '<a href="/first">First</a>',
            ScanOptions(max_pages=2, overall_timeout_seconds=1, retry_attempts=0),
        ),
        (
            '<img src="/first">First</img>',
            ScanOptions(
                max_pages=1,
                max_resources=1,
                overall_timeout_seconds=1,
                retry_attempts=0,
            ),
        ),
    ],
    ids=("page", "resource"),
)
def test_overall_timeout_after_terminal_collection_request_failure_is_recorded(
    tmp_path,
    entry_html,
    options,
) -> None:
    class ControlledClock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += seconds

    clock = ControlledClock()
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=entry_html,
            )
        if request.url.path == "/first":
            clock.advance(2.0)
            raise httpx.ConnectError("terminal failure", request=request)
        raise AssertionError("No target request may start after the deadline")

    service = _service(tmp_path, handler, clock=clock)
    scan = service.start_scan("http://127.0.0.1/", options)

    assert calls == ["/", "/first"]
    assert scan.status == "completed_with_warnings"
    assert scan.error_code is None
    assert scan.asset_count == 1
    assert scan.evidence_count == 1
    codes = [failure.code for failure in scan.failures]
    assert codes.count("network_retry_exhausted") == 1
    assert codes.count("overall_timeout") == 1


def test_collection_timeout_warning_is_idempotent_when_running_scan_reenters(tmp_path) -> None:
    class ControlledClock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += seconds

    clock = ControlledClock()

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<a href="/first">First</a>',
            )
        if request.url.path == "/first":
            clock.advance(2.0)
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<title>First</title>",
            )
        raise AssertionError("Unexpected target request")

    service = _service(tmp_path, handler, clock=clock)
    initial = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(max_pages=2, overall_timeout_seconds=1, retry_attempts=0),
    )
    with session_scope() as session:
        run = session.get(ScanRun, initial.id)
        assert run is not None
        run.status = "running"
        run.finished_at = None

    service.execute_scan(initial.id)
    reentered = service.get_scan(initial.id)

    assert reentered.status == "completed_with_warnings"
    assert [failure.code for failure in reentered.failures].count("overall_timeout") == 1


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
