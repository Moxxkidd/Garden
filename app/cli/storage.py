"""Local private backups: never switch the running directory automatically."""

from pathlib import Path

import typer

from app.cli.projects import emit, guarded
from app.services.local_data import LocalDataService

app = typer.Typer(help="本地占用、私有备份、隔离恢复与预览清理。")


@app.command("usage")
@guarded
def usage():
    emit(LocalDataService().usage())


@app.command("backup")
@guarded
def backup(destination: Path):
    emit(LocalDataService().backup(destination))


@app.command("restore")
@guarded
def restore(backup_directory: Path, destination: Path):
    emit(LocalDataService().restore(backup_directory, destination))


@app.command("cleanup")
@guarded
def cleanup(
    retention_days: int = typer.Option(30, min=0, max=3650),
    archived_project_ids: str = "",
    execute: bool = False,
):
    service = LocalDataService()
    preview = service.cleanup_preview(
        retention_days=retention_days,
        archived_project_ids=[int(v) for v in archived_project_ids.split()],
    )
    emit(preview)
    if (
        execute
        and preview["count"]
        and typer.confirm(f"删除上述 {preview['count']} 个文件（{preview['bytes']} 字节）？")
    ):
        emit(service.cleanup_confirm(preview["token"]))
