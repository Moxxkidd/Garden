"""Read-only diagnostics that remain available when application settings are broken."""

from typing import Annotated

import typer

from app.cli.utils import console
from app.services.doctor import diagnose


def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="输出脱敏 JSON。")] = False,
    ui_port: Annotated[
        int | None, typer.Option("--ui-port", min=1, max=65535, help="检查指定本机端口。")
    ] = None,
) -> None:
    """检查本地环境并给出处理建议；不自动修复或访问扫描目标。"""
    result = diagnose(ui_port=ui_port)
    if json_output:
        typer.echo(result.model_dump_json())
    else:
        console.print("Garden 环境自检（只读）", markup=False)
        labels = {"ok": "正常", "action_required": "需要处理", "unknown": "无法确认"}
        names = {
            "installation": "安装与版本",
            "configuration": "配置来源",
            "database": "数据库",
            "service": "本地服务",
            "browser": "浏览器",
            "directory_reports": "报告目录",
            "directory_storage": "存储目录",
            "directory_runtime": "运行目录",
        }
        for check in result.checks:
            console.print(
                f"[{labels[check.status]}] {names[check.id]}：{check.message}", markup=False
            )
            if check.next_step:
                console.print(f"  下一步：{check.next_step}", markup=False)
    raise typer.Exit(result.exit_code)
