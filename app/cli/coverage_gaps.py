"""Shared plain-text rendering for both CLI result flows."""

from app.cli.utils import console
from app.services.coverage_gaps import COVERAGE_GAP_NOTE


def print_coverage_gaps(gaps) -> None:
    console.print("覆盖缺口解释", markup=False)
    console.print(COVERAGE_GAP_NOTE, markup=False)
    if gaps is None:
        console.print("当前响应未提供结构化解释；覆盖影响数量未知。", markup=False)
    for gap in gaps or []:
        console.print(f"- {gap.summary}", markup=False)
        if gap.samples:
            console.print("  脱敏样例：" + "；".join(gap.samples), markup=False)
        if gap.next_step:
            console.print(f"  下一步：{gap.next_step}", markup=False)
