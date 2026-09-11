"""A finished run must not imply complete coverage, on any result surface."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.cli.coverage import _print_result as print_coverage
from app.cli.scan import _print_result as print_scan
from app.db.bootstrap import session_scope
from app.schemas.scan import ScanOptions
from app.services.scan_application import InlineScanDispatcher, ScanApplicationService
from tests.test_authenticated_coverage_pipeline import (
    MissingUserContextService,
    _pipeline,
    _queued_authenticated_run,
)
from tests.test_url_scan_pipeline import _service


@pytest.mark.parametrize(
    ("max_pages", "expected_status", "coverage_text"),
    [
        (10, "completed", "本次范围内采集完成（仅匿名上下文）"),
        (1, "completed_with_warnings", "覆盖不完整：存在未覆盖内容或采集失败"),
    ],
)
def test_quick_progress_and_coverage_are_distinct_on_all_surfaces(
    tmp_path, app, capsys, max_pages, expected_status, coverage_text
):
    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<h1>Local demo</h1><a href="/next">Next</a>',
        )

    service = _service(tmp_path, handler)
    scan = service.start_scan("http://127.0.0.1:8080/", ScanOptions(max_pages=max_pages))
    assert scan.status == expected_status
    assert scan.progress == 100
    print_scan(scan)
    terminal = capsys.readouterr().out
    report = service.read_report(scan.id)
    with TestClient(app) as client:
        app.state.scan_service = service
        payload = client.get(f"/api/scans/{scan.id}").json()
        detail = client.get(f"/scans/{scan.id}").text
        listing = client.get("/scans").text
        dashboard = client.get("/").text
    assert payload["completeness"] == "legacy_single_context"
    for text in (terminal, report, detail):
        assert coverage_text in text
        assert "不代表覆盖完整" in text
        assert "100%" in text
    for text in (listing, dashboard):
        assert "执行进度" in text
        assert "不代表覆盖完整" in text
    count = "2 类关注项，4 条原始观察" if max_pages == 10 else "2 类关注项，2 条原始观察"
    for text in (terminal, report, detail):
        assert count in text


@pytest.mark.parametrize(
    ("missing_user", "status", "completeness", "coverage_text"),
    [
        (False, "completed", "complete", "本次范围内三上下文采集完成"),
        (True, "incomplete", "missing_user_context", "覆盖不完整：缺少有效的普通用户上下文"),
    ],
)
def test_authenticated_api_report_web_and_cli_agree(
    tmp_path, app, capsys, missing_user, status, completeness, coverage_text
):
    pipeline = _pipeline(
        tmp_path, context_service=MissingUserContextService() if missing_user else None
    )
    with session_scope() as session:
        run = _queued_authenticated_run(session)
        pipeline.execute_authenticated(session, run.id)
        run_id = run.id
    service = ScanApplicationService(pipeline=pipeline, dispatcher=InlineScanDispatcher())
    scan = service.get_assessment(run_id)
    assert (scan.status, scan.completeness, scan.progress) == (status, completeness, 100)
    differences = service.list_coverage_differences(run_id)
    if missing_user:
        assert all(item.user_state == "unknown" for item in differences)
    print_coverage(scan, differences)
    terminal = capsys.readouterr().out
    with TestClient(app) as client:
        app.state.scan_service = service
        payload = client.get(f"/api/assessments/{run_id}").json()
        report = client.get(f"/api/assessments/{run_id}/report").text
        detail = client.get(f"/scans/{run_id}").text
    assert payload["completeness"] == completeness
    assert f"- 完整性：{completeness}\n" in report
    for text in (terminal, report, detail):
        assert coverage_text in text
        assert "不代表覆盖完整" in text
        assert "100%" in text
        assert "2 类关注项，" in text
    assert "- 完整性：pending" not in Path(scan.report_path).read_text()


def test_report_write_failure_does_not_persist_100_percent_or_complete(tmp_path):
    pipeline = _pipeline(tmp_path)
    with session_scope() as session:
        run = _queued_authenticated_run(session)
        # An actual filesystem failure at the final stage, after collection succeeded.
        (tmp_path / "reports" / f"scan-{run.id}.md").mkdir(parents=True)
        pipeline.execute_authenticated(session, run.id)
        session.refresh(run)
        assert run.status == "failed"
        assert run.completeness == "incomplete"
        assert run.progress < 100
        assert run.report_path is None
