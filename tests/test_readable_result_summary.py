from datetime import datetime, timezone

import pytest

from app.services import scan_result_presentation as presentation


@pytest.mark.parametrize(
    ("status", "count", "expected"),
    [
        ("running", 0, "尚未形成最终结果"),
        ("queued", 0, "尚未形成最终结果"),
        ("running", 6, "暂定"),
        ("completed", 0, "不代表目标安全"),
        ("failed", 0, "不能据此判断是否存在问题"),
        ("incomplete", 0, "不能据此判断是否存在问题"),
        ("completed", 6, "待复核"),
    ],
)
def test_summary_distinguishes_pending_empty_partial_and_observed(status, count, expected):
    summary = presentation.build_result_summary(
        status=status,
        completeness="legacy_single_context",
        mode="quick",
        raw_count=count,
        group_count=None,
    )
    assert expected in summary.findings
    assert "分类数未提供" in summary.findings
    assert summary.next_steps


def test_active_summary_does_not_recommend_restart_from_partial_diagnostics():
    summary = presentation.build_result_summary(
        status="running",
        completeness=None,
        mode="quick",
        raw_count=0,
        group_count=0,
        diagnostics=[("collect", "overall_timeout")],
    )
    assert "等待任务结束" in summary.next_steps[0]
    assert "重新扫描" not in "".join(summary.next_steps)


def test_summary_guidance_deduplicates_and_does_not_echo_unknown_codes():
    summary = presentation.build_result_summary(
        status="incomplete",
        completeness="missing_admin_context",
        mode="authenticated_coverage",
        raw_count=0,
        group_count=0,
        diagnostics=[("context", "authentication_session_unavailable")] * 2
        + [("collect", "secret=NEVER_ECHO")],
    )
    assert len(summary.next_steps) == 2
    assert "查看诊断" in summary.next_steps[1]
    assert "登录地址" in summary.next_steps[0]
    assert "管理员" in summary.coverage
    assert "NEVER_ECHO" not in str(summary)


@pytest.mark.parametrize("mode", ["quick", "authenticated_coverage"])
def test_report_opens_with_readable_summary_even_when_empty(mode, tmp_path):
    from app.models.scan_run import ScanRun
    from app.services.scan_reporting import ScanReportService

    run = ScanRun(
        id=8,
        input_url="https://example.test/",
        normalized_url="https://example.test/",
        mode=mode,
        status="completed",
        progress=100,
        completeness="complete",
        options={},
    )
    service = ScanReportService(tmp_path)
    render = service._render if mode == "quick" else service._render_authenticated
    report = "\n".join(render(run, datetime.now(timezone.utc)))
    opening = report.split("## 执行摘要")[0]
    assert "## 结果摘要" in opening
    assert "主要发现" in opening
    assert "不代表目标安全" in opening
    assert "覆盖限制" in opening
    assert "下一步" in opening


@pytest.mark.parametrize("mode", ["quick", "authenticated_coverage"])
def test_terminal_summary_precedes_detail_and_explains_unknown_coverage(mode, capsys):
    from app.cli.coverage import _print_result as print_coverage
    from app.cli.scan import _print_result as print_scan
    from app.schemas.scan import ScanRunView

    view = ScanRunView(
        id=8,
        input_url="https://example.test/",
        normalized_url="https://example.test/",
        status="completed",
        mode=mode,
        completeness=None,
        current_stage="finished",
        progress=100,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        finding_count=0,
        finding_group_count=None,
    )
    if mode == "quick":
        print_scan(view)
    else:
        print_coverage(view, [])
    output = capsys.readouterr().out
    assert output.index("结果摘要") < output.index("Garden ")
    assert "不能据此判断是否存在问题" in output
    assert "分类数未提供" in output
    assert "覆盖完整性未知" in output
    assert "查看诊断和执行阶段" in output
