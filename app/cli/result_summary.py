"""Compact, plain-text result summary before detailed terminal output."""

from app.cli.utils import console
from app.services.scan_result_presentation import summarize_scan_view


def print_result_summary(view) -> None:
    summary = summarize_scan_view(view)
    console.print("结果摘要", markup=False)
    console.print(f"主要发现：{summary.findings}", markup=False)
    console.print(f"覆盖限制：{summary.coverage}", markup=False)
    console.print("下一步：", markup=False)
    for step in summary.next_steps:
        console.print(f"- {step}", markup=False)
    console.print()
