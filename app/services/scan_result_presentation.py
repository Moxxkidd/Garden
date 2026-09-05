"""Side-effect-free result wording shared by terminal, Web, and reports."""


def format_finding_count(raw_count: int, group_count: int | None) -> str:
    if group_count is None:
        return f"{raw_count} 条原始观察（分类数未提供）"
    return f"{group_count} 类（{raw_count} 条原始观察）"
