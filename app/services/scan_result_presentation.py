"""Side-effect-free result wording shared by terminal, Web, and reports."""

_DIAGNOSTIC_HINTS = {
    ("context", "authentication_session_mismatch"): (
        "请检查凭据档案所属 Target 和 user/admin 角色，选择与当前目标匹配的档案后重新提交。"
    ),
    ("context", "authentication_session_unavailable"): (
        "请通过 coverage 向导检查凭据档案、登录地址和登录后验证地址，必要时重新输入凭据。"
    ),
    ("collect", "cross_origin_redirect_blocked"): (
        "跳转目标超出当前同源边界；请先确认其是否在授权范围内，如需采集，应以该目标作为新入口单独扫描。"
    ),
    ("collect", "coverage_limit_reached"): (
        "请根据报告中的命中限制检查 --max-pages、--max-resources 或 --max-depth；"
        "调整后重新扫描会创建新任务。"
    ),
    ("collect", "overall_timeout"): (
        "可增加 --overall-timeout 后重新扫描；重新扫描会创建新任务，不是断点续扫。"
    ),
}


def diagnostic_hint(stage: str, code: str) -> str | None:
    return _DIAGNOSTIC_HINTS.get((stage, code))


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


def execution_summary(status: str) -> str:
    """Describe lifecycle only; coverage is explained separately."""
    return {
        "queued": "任务排队中",
        "running": "任务执行中",
        "completed": "任务执行结束",
        "completed_with_warnings": "任务执行结束（有告警）",
        "incomplete": "任务执行结束（覆盖不完整）",
        "failed": "任务执行失败",
        "interrupted": "任务已中断",
    }.get(status, "任务状态未知")
