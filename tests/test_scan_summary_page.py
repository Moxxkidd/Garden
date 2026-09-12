from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.schemas.scan import ScanFailureView, ScanRunView


@pytest.fixture
def render_summary(app):
    def render(status="completed", completeness="legacy_single_context", **overrides):
        values = dict(
            id=7,
            input_url="http://127.0.0.1/",
            normalized_url="http://127.0.0.1/",
            status=status,
            completeness=completeness,
            current_stage="finished",
            progress=100,
            retry_count=0,
            created_at=datetime.now(timezone.utc),
            finding_count=6,
            finding_group_count=2,
            report_path="reports/scan-7.md",
        )
        values.update(overrides)
        view = ScanRunView(**values)
        with TestClient(app) as client:
            original = app.state.scan_service
            app.state.scan_service = SimpleNamespace(
                get_scan=lambda run_id: view,
                read_report=lambda run_id: "# Original report\nRaw evidence stays available.",
            )
            try:
                response = client.get("/scans/7")
            finally:
                app.state.scan_service = original
        assert response.status_code == 200
        return response.text

    return render


@pytest.mark.parametrize(
    ("status", "label"),
    [
        ("completed", "任务执行结束"),
        ("completed_with_warnings", "任务执行结束（有告警）"),
        ("incomplete", "任务执行结束（覆盖不完整）"),
        ("failed", "任务执行失败"),
        ("interrupted", "任务已中断"),
        ("queued", "任务排队中"),
        ("running", "任务执行中"),
    ],
)
def test_summary_answers_four_questions_before_technical_details(render_summary, status, label):
    html = render_summary(status=status)
    summary = html.split('id="result-summary"', 1)[1].split("</section>", 1)[0]
    for text in (
        "执行状态",
        "覆盖情况",
        "关注项",
        "建议操作",
        label,
        "2 类关注项，6 条原始观察",
        "不代表覆盖完整",
    ):
        assert text in summary
    assert html.index('id="result-summary"') < html.index('id="execution-details"')
    assert 'href="/api/scans/7/report?download=true"' in html
    assert "Raw evidence stays available." in html
    assert '<details id="report-details"' in html
    assert '<details id="report-details" open' not in html


def test_active_task_keeps_stages_open_and_refreshes_even_with_report(render_summary):
    html = render_summary(status="running", current_stage="collect", progress=50)
    assert '<details id="execution-details" open' in html
    assert "window.setTimeout" in html
    assert "覆盖完整性尚未确定" in html


def test_finished_task_collapses_stages_and_preserves_unknown_counts(render_summary):
    html = render_summary(completeness=None, finding_group_count=None)
    assert '<details id="execution-details" open' not in html
    assert "分类数未提供" in html
    assert "覆盖完整性未知" in html


def test_summary_deduplicates_known_guidance_and_keeps_unknown_diagnostics(render_summary):
    failure = ScanFailureView(
        stage="collect",
        code="coverage_limit_reached",
        message="budget reached",
        retryable=False,
        attempt=1,
        occurred_at=datetime.now(timezone.utc),
    )
    html = render_summary(
        status="completed_with_warnings", report_path=None, failures=[failure, failure.model_copy()]
    )
    summary = html.split('id="result-summary"', 1)[1].split("</section>", 1)[0]
    assert summary.count("调整后重新扫描会创建新任务") == 1
    assert "覆盖不完整" in summary
    html = render_summary(
        status="failed",
        report_path=None,
        error_code="unknown_error",
        error_message="Unrecognized failure",
    )
    assert "Unrecognized failure" in html
    assert "查看诊断" in html
    assert 'id="report-details"' not in html
