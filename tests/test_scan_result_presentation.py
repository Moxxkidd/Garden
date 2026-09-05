import pytest

from app.services.scan_result_presentation import (
    coverage_summary,
    diagnostic_hint,
    format_finding_count,
)


def test_cross_origin_guidance():
    assert diagnostic_hint("collect", "cross_origin_redirect_blocked") == (
        "跳转目标超出当前同源边界；请先确认其是否在授权范围内，如需采集，应以该目标作为新入口单独扫描。"
    )


def test_budget_guidance():
    assert diagnostic_hint("collect", "coverage_limit_reached") == (
        "请根据报告中的命中限制检查 --max-pages、--max-resources 或 --max-depth；"
        "调整后重新扫描会创建新任务。"
    )


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


def test_unknown_code_and_unrelated_stage_have_no_speculative_advice():
    assert diagnostic_hint("report", "unrecognized_failure") is None
    assert diagnostic_hint("report", "overall_timeout") is None
    assert diagnostic_hint("collect", "token=TEST_SECRET") is None


def test_collection_timeout_explains_new_run():
    assert diagnostic_hint("collect", "overall_timeout") == (
        "可增加 --overall-timeout 后重新扫描；重新扫描会创建新任务，不是断点续扫。"
    )
