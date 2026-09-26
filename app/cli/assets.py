"""Inspect and export stored assets without contacting scan targets."""

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table
from rich.text import Text

from app.cli.utils import console, handle_cli_error
from app.core.errors import GardenError, InputValidationError
from app.db.bootstrap import session_scope
from app.schemas.assets import AssetQuery
from app.services.asset_catalog import KINDS, OBSERVATIONS, AssetCatalogService
from app.services.asset_export import export_assets

app = typer.Typer(help="查看和导出统一资产清单（只读取已有采集记录）。")


def _query(**values):
    from pydantic import ValidationError

    try:
        return AssetQuery(**values)
    except ValidationError as error:
        fields = ", ".join(dict.fromkeys(str(e["loc"][0]) for e in error.errors()))
        raise InputValidationError(f"资产查询参数无效：{fields}。") from None


@app.command("list")
def list_assets(
    source: str = typer.Option(..., help="scan 或 inventory"),
    run_id: int = typer.Option(..., min=1),
    kind: str | None = typer.Option(None),
    context: str | None = typer.Option(None),
    observation: str | None = typer.Option(None),
    q: str = typer.Option(""),
    sort: str = typer.Option("id"),
    order: str = typer.Option("asc"),
    page: int = typer.Option(1, min=1),
    page_size: int = typer.Option(50, min=1, max=200),
    as_json: bool = typer.Option(False, "--json", help="输出可机器读取的 JSON。"),
) -> None:
    try:
        query = _query(
            source=source,
            run_id=run_id,
            kind=kind,
            context=context,
            observation=observation,
            q=q,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        )
        with session_scope() as session:
            result = AssetCatalogService().list(session, query)
        if as_json:
            typer.echo(result.model_dump_json(indent=2))
            return
        console.print(
            f"统一资产清单 · {source} #{run_id} · 匹配 {result.matched} / 总记录 {result.total}",
            markup=False,
        )
        console.print(result.count_note, markup=False)
        console.print(result.scope.coverage_note, markup=False)
        if result.scope.live:
            console.print("任务仍在执行，记录及计数可能变化。")
        table = Table("资产 ID", "类型", "方法", "URL", "身份", "响应状态", "观察")
        for row in result.items:
            table.add_row(
                *(
                    Text(v)
                    for v in [
                        row.asset_id,
                        KINDS[row.kind],
                        row.method or "未知",
                        row.url,
                        row.context,
                        ", ".join(map(str, row.status_codes)) or "未知",
                        OBSERVATIONS[row.observation],
                    ]
                )
            )
        console.print(table)
        console.print(
            f"第 {page} 页 · 本页 {len(result.items)} 条；export 导出全部匹配记录。", markup=False
        )
        if not result.items:
            console.print("没有匹配记录；不代表目标没有资产。")
    except GardenError as error:
        handle_cli_error(error)


@app.command("export")
def export_asset_list(
    source: str = typer.Option(..., help="scan 或 inventory"),
    run_id: int = typer.Option(..., min=1),
    output: Annotated[Path, typer.Option()] = ...,
    format: str = typer.Option("json"),
    kind: str | None = typer.Option(None),
    context: str | None = typer.Option(None),
    observation: str | None = typer.Option(None),
    q: str = typer.Option(""),
    sort: str = typer.Option("id"),
    order: str = typer.Option("asc"),
) -> None:
    try:
        if format not in {"json", "csv"}:
            raise InputValidationError("导出格式必须是 json 或 csv。")
        query = _query(
            source=source,
            run_id=run_id,
            kind=kind,
            context=context,
            observation=observation,
            q=q,
            sort=sort,
            order=order,
        )
        with session_scope() as session:
            content = export_assets(session, query, format)
        with output.open("x", encoding="utf-8", newline="") as handle:
            handle.write(content)
        console.print(f"已导出全部匹配记录：{output}", markup=False)
    except FileExistsError:
        handle_cli_error(InputValidationError("输出文件已存在，请选择新路径。"))
    except OSError:
        handle_cli_error(InputValidationError("无法写入输出文件，请检查目录和权限。"))
    except GardenError as error:
        handle_cli_error(error)
