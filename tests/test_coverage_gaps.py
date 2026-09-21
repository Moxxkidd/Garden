"""Coverage explanations observed through the scan application boundary."""

import httpx
import pytest

from app.schemas.scan import ScanOptions
from tests.test_url_scan_pipeline import _service


def test_budget_gaps_count_unique_unrequested_urls_and_redact_samples(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        html = (
            '<a href="/a">A</a><a href="/b?token=private-value">B</a>'
            '<a href="/b?token=private-value">B again</a><img src="/logo.png">'
            if request.url.path == "/"
            else '<a href="/deep">Deep</a>'
        )
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    service = _service(tmp_path, handler)
    scan = service.start_scan(
        "http://127.0.0.1/", ScanOptions(max_pages=2, max_resources=0, max_depth=1)
    )

    assert calls == ["/", "/a"]
    assert scan.status == "completed_with_warnings"
    assert [(gap.reason, gap.count) for gap in scan.coverage_gaps] == [
        ("max_pages", 1),
        ("max_resources", 1),
        ("max_depth", 1),
    ]
    assert all(gap.context == "anonymous" for gap in scan.coverage_gaps)
    assert "private-value" not in str(scan.coverage_gaps)
    assert "[REDACTED]" in scan.coverage_gaps[0].samples[0]
    assert "--max-pages" in scan.coverage_gaps[0].next_step
    assert "未请求 URL：1" in scan.coverage_gaps[0].summary
    assert service.get_scan(scan.id).coverage_gaps == scan.coverage_gaps


def test_legacy_budget_warning_does_not_guess_counts_from_message(tmp_path):
    from app.db.bootstrap import session_scope
    from app.models.scan_run import ScanRun

    service = _service(
        tmp_path,
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"}, text='<a href="/next">Next</a>'
        ),
    )
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(max_pages=1))
    with session_scope() as session:
        run = session.get(ScanRun, scan.id)
        run.failures[0].coverage_details = None
        run.failures[0].message = "未覆盖数量：900；token=private-value"
    gap = service.get_scan(scan.id).coverage_gaps[0]
    assert gap.reason == "coverage_limit_reached"
    assert gap.count is None
    assert "数量未知" in gap.summary
    assert "900" not in str(gap)
    assert "private-value" not in str(gap)


def test_attempted_request_failure_is_not_counted_as_unrequested_budget_gap(tmp_path):
    def handler(request):
        if request.url.path == "/broken":
            raise httpx.ReadTimeout("private-value", request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=('<a href="/broken">Broken</a><a href="/later">Later</a>'),
        )

    service = _service(tmp_path, handler)
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(max_pages=2, retry_attempts=0))
    budget = next(gap for gap in scan.coverage_gaps if gap.reason == "max_pages")
    failed = next(gap for gap in scan.coverage_gaps if gap.reason == "request_failed")
    assert budget.count == 1
    assert failed.count is None
    assert "已尝试请求" in failed.summary
    assert "未请求" not in failed.summary
    assert "private-value" not in str(failed)


def test_failed_identity_has_unknown_coverage_without_exposing_exception(
    app, tmp_path, monkeypatch
):
    from app.db.bootstrap import session_scope
    from app.models.scan_context import ScanContext
    from app.models.scan_run import ScanRun

    service = _service(tmp_path, lambda request: httpx.Response(200))
    with session_scope() as session:
        run = ScanRun(
            mode="authenticated_coverage",
            input_url="http://127.0.0.1/",
            normalized_url="http://127.0.0.1/",
            status="incomplete",
            current_stage="finished",
            progress=100,
            completeness="missing_user_context",
            options={},
        )
        session.add(run)
        session.flush()
        session.add(
            ScanContext(
                scan_run_id=run.id,
                kind="user",
                status="failed",
                collection_status="failed",
                completeness="incomplete",
                error_code="authentication_session_unavailable",
                error_message="password=private-value",
            )
        )
        session.flush()
        service.pipeline.report_service.generate(session, run.id)
        run_id = run.id
    gaps = service.get_scan(run_id).coverage_gaps
    user_gap = next(gap for gap in gaps if gap.context == "user")
    assert user_gap.count is None
    assert "普通用户" in user_gap.summary and "数量未知" in user_gap.summary
    assert "登录后验证地址" in user_gap.next_step
    assert "private-value" not in str(gaps)
    assert all(gap.count is None for gap in gaps)
    report = service.read_report(run_id)
    assert user_gap.summary in report
    assert "private-value" not in report
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    import app.cli.coverage as coverage_cli
    from app.cli.main import app as cli_app

    with TestClient(app) as client:
        app.state.scan_service = service
        api = client.get(f"/api/assessments/{run_id}").json()
        page = client.get(f"/scans/{run_id}").text
    assert (
        next(g for g in api["coverage_gaps"] if g["context"] == "user")["summary"]
        == user_gap.summary
    )
    assert user_gap.summary in page
    monkeypatch.setattr(
        coverage_cli,
        "WebRuntimeManager",
        lambda **kwargs: SimpleNamespace(
            ensure=lambda **kwargs: SimpleNamespace(base_url="http://127.0.0.1:8000")
        ),
    )
    monkeypatch.setattr(
        coverage_cli,
        "LocalScanApi",
        lambda base_url: SimpleNamespace(
            start_assessment=lambda request: service.get_assessment(run_id),
            list_coverage_differences=lambda assessment_id: [],
        ),
    )
    result = CliRunner().invoke(
        cli_app,
        [
            "coverage",
            "http://127.0.0.1/",
            "--non-interactive",
            "--user-profile",
            "1",
            "--admin-profile",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "".join(user_gap.summary.split()) in "".join(result.output.split())
    assert "private-value" not in result.output


def test_api_page_cli_and_saved_report_share_gap_explanation(app, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    import app.cli.scan as scan_cli
    from app.cli.main import app as cli_app

    service = _service(
        tmp_path,
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"}, text='<a href="/next">Next</a>'
        ),
    )
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(max_pages=1))
    summary = scan.coverage_gaps[0].summary
    report = service.read_report(scan.id)
    assert summary in report
    with TestClient(app) as client:
        app.state.scan_service = service
        api = client.get(f"/api/scans/{scan.id}").json()
        page = client.get(f"/scans/{scan.id}").text
    assert api["coverage_gaps"][0]["summary"] == summary
    assert 'id="coverage-gaps"' in page
    assert summary in page
    monkeypatch.setattr(
        scan_cli,
        "WebRuntimeManager",
        lambda **kwargs: SimpleNamespace(
            ensure=lambda **kwargs: SimpleNamespace(base_url="http://127.0.0.1:8000")
        ),
    )
    monkeypatch.setattr(
        scan_cli,
        "LocalScanApi",
        lambda base_url: SimpleNamespace(start_scan=lambda url, options: scan),
    )
    result = CliRunner().invoke(cli_app, ["scan", "--url", "http://127.0.0.1/"])
    assert result.exit_code == 0, result.output
    assert "".join(summary.split()) in "".join(result.output.split())


@pytest.mark.parametrize(
    "details",
    [
        None,
        {},
        {"version": 2, "items": []},
        {"version": 1, "items": [{"reason": "max_pages", "count": -1, "limit": 1}]},
        {"version": 1, "items": [{"reason": "max_pages", "count": "3", "limit": 1}]},
    ],
)
def test_missing_or_unrecognized_details_remain_unknown(tmp_path, details):
    from app.db.bootstrap import session_scope
    from app.models.scan_run import ScanRun

    service = _service(
        tmp_path,
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"}, text='<a href="/next">Next</a>'
        ),
    )
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(max_pages=1))
    with session_scope() as session:
        session.get(ScanRun, scan.id).failures[0].coverage_details = details
    gap = service.get_scan(scan.id).coverage_gaps[0]
    assert gap.count is None
    assert gap.samples == []
    assert "数量未知" in gap.summary


def test_samples_are_bounded_and_off_origin_links_are_not_budget_gaps(tmp_path):
    links = "".join(f'<a href="/page/{i}?secret=value-{i}">Page</a>' for i in range(8))
    links += '<a href="http://external.invalid/private">External</a>'
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text=links)

    service = _service(tmp_path, handler)
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(max_pages=1))
    assert calls == ["http://127.0.0.1/"]
    assert len(scan.coverage_gaps) == 1
    gap = scan.coverage_gaps[0]
    assert gap.count == 8
    assert len(gap.samples) == 3
    assert "external.invalid" not in str(gap)
    assert "value-" not in str(gap)


def test_completed_bounded_scan_does_not_invent_zero_site_wide_gaps(tmp_path):
    service = _service(
        tmp_path,
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"}, text="<title>Only page</title>"
        ),
    )
    scan = service.start_scan("http://127.0.0.1/")
    assert scan.coverage_gaps == []
    assert "没有条目不代表整个站点已覆盖" in service.read_report(scan.id)


def test_requested_redirect_alias_is_not_a_depth_gap(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(302, headers={"location": "/home"})
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text='<a href="/">Home</a>'
        )

    service = _service(tmp_path, handler)
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(max_pages=10, max_depth=5))
    assert calls == ["/", "/home"]
    assert scan.coverage_gaps == []
    assert "发现深度上限" not in service.read_report(scan.id)


def test_failed_redirect_destination_is_attempted_not_a_budget_gap(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=('<a href="/a">A</a><a href="/b">B</a>'),
            )
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "/b"})
        raise httpx.ReadTimeout("fixture", request=request)

    service = _service(tmp_path, handler)
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(max_pages=2, retry_attempts=0))
    assert calls == ["/", "/a", "/b"]
    assert [gap.reason for gap in scan.coverage_gaps] == ["request_failed"]
    assert "未请求 URL：1" not in service.read_report(scan.id)


def test_request_observer_does_not_record_blocked_redirect_destinations(tmp_path):
    from app.services.scan_network import FetchError

    calls, attempts = [], []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://external.invalid/private"})

    service = _service(tmp_path, handler)
    with pytest.raises(FetchError, match="same-origin"):
        service.pipeline.gateway.fetch(
            "http://127.0.0.1/start",
            ScanOptions(retry_attempts=0),
            expected_origin=("http", "127.0.0.1", 80),
            on_request_attempt=attempts.append,
        )
    assert attempts == calls == ["http://127.0.0.1/start"]


def test_request_observer_runs_only_after_request_guard(tmp_path):
    attempts, calls = [], []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200)

    def guard():
        raise RuntimeError("stopped before request")

    service = _service(tmp_path, handler)
    with pytest.raises(RuntimeError, match="stopped before request"):
        service.pipeline.gateway.fetch(
            "http://127.0.0.1/start",
            ScanOptions(retry_attempts=0),
            before_request=guard,
            on_request_attempt=attempts.append,
        )
    assert attempts == calls == []
