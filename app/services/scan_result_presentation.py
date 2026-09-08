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
    return f"{group_count} 类（{raw_count} 条原始观察）"
