import pytest

from app.services.scan_result_presentation import coverage_summary, format_finding_count


def test_finding_count_text():
    assert format_finding_count(104, 2) == "2 类关注项，104 条原始观察"


def test_empty_finding_count():
    assert format_finding_count(0, 0) == "0 类关注项，0 条原始观察"


def test_legacy_finding_count():
    assert format_finding_count(104, None) == "104 条原始观察（分类数未提供）"


@pytest.mark.parametrize(
    ("status", "completeness", "mode", "expected"),
    [
        ("running", "complete", "authenticated_coverage", "尚未确定"),
        ("queued", "legacy_single_context", "quick", "尚未确定"),
        ("incomplete", "missing_admin_context", "authenticated_coverage", "缺少有效的管理员"),
        ("failed", "complete", "authenticated_coverage", "任务失败或已中断"),
        ("interrupted", "legacy_single_context", "quick", "任务失败或已中断"),
        ("completed_with_warnings", "complete", "authenticated_coverage", "存在未覆盖内容"),
        ("completed", None, "quick", "覆盖完整性未知"),
        ("completed", "future_value", "authenticated_coverage", "覆盖完整性未知"),
    ],
)
def test_coverage_explanation_does_not_infer_completeness(status, completeness, mode, expected):
    assert expected in coverage_summary(status, completeness, mode)
