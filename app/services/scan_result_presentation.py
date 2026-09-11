"""Side-effect-free result wording shared by terminal, Web, and reports."""


def format_finding_count(raw_count: int, group_count: int | None) -> str:
    if group_count is None:
        return f"{raw_count} 条原始观察（分类数未提供）"
    return f"{group_count} 类关注项，{raw_count} 条原始观察"


def format_execution_progress(progress: int) -> str:
    return f"{progress}%（执行进度，不代表覆盖完整）"


def coverage_summary(status: str, completeness: str | None, mode: str) -> str:
    """Explain bounded coverage without turning completion or missing data into safety."""
    if status in {"queued", "running"}:
        return "评估中：覆盖完整性尚未确定。"
    if completeness == "missing_user_context":
        return "覆盖不完整：缺少有效的普通用户上下文；未观察到的资产仍无法判断。"
    if completeness == "missing_admin_context":
        return "覆盖不完整：缺少有效的管理员上下文；未观察到的资产仍无法判断。"
    if status in {"failed", "interrupted"}:
        return "覆盖不完整：任务失败或已中断，不能据此判断目标安全。"
    if status in {"incomplete", "completed_with_warnings"} or completeness == "incomplete":
        return "覆盖不完整：存在未覆盖内容或采集失败，请查看诊断。"
    if status == "completed":
        if mode == "quick" and completeness in {"legacy_single_context", "complete"}:
            return "本次范围内采集完成（仅匿名上下文）；不代表整个站点已覆盖。"
        if mode == "authenticated_coverage" and completeness == "complete":
            return "本次范围内三上下文采集完成；不代表整个站点已覆盖。"
    return "覆盖完整性未知：当前响应未提供可确认的完整性信息。"
