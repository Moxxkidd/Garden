"""Manage persistent project assets without extending scan authorization."""

import json
from functools import wraps
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.table import Table

from app.cli.utils import console, handle_cli_error
from app.core.errors import GardenError, InputValidationError, ResourceNotFoundError
from app.db.bootstrap import session_scope
from app.models.project import ProjectRun
from app.schemas.projects import (
    ProjectCorrectionRequest,
    ProjectEntryCreate,
    ProjectImportRequest,
    ProjectQuery,
    ProjectSourceRef,
    ProjectView,
)
from app.services.project_catalog import ProjectCatalogService
from app.services.project_comparison import ProjectComparisonService
from app.services.project_curation import ProjectCurationService
from app.services.project_export import export_project
from app.services.project_ingestion import ProjectIngestionService
from app.services.project_presentation import correction_view, entry_view, run_view
from app.services.projects import ProjectService

app = typer.Typer(help="项目资产库：跨任务历史、比较与可撤销整理。")
projects = ProjectService()


def guarded(function):
    @wraps(function)
    def invoke(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValidationError, ValueError):
            handle_cli_error(InputValidationError("项目参数无效，请检查字段与输入大小。"))
        except GardenError as error:
            handle_cli_error(error)

    return invoke


def emit(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@app.command("create")
@guarded
def create(name: str, description: str = ""):
    with session_scope() as session:
        emit(ProjectView.model_validate(projects.create(session, name, description)))


@app.command("list")
@guarded
def list_projects():
    with session_scope() as session:
        emit([ProjectView.model_validate(p).model_dump() for p in projects.list(session)])


@app.command("show")
@guarded
def show(project_id: int):
    with session_scope() as session:
        emit(
            dict(
                project=ProjectView.model_validate(projects.get(session, project_id)).model_dump(),
                entries=[entry_view(session, e) for e in projects.entries(session, project_id)],
                runs=[run_view(r) for r in ProjectIngestionService().runs(session, project_id)],
            )
        )


@app.command("archive")
@guarded
def archive(project_id: int, revision: int = typer.Option(..., min=0)):
    with session_scope() as session:
        emit(ProjectView.model_validate(projects.archive(session, project_id, revision)))


@app.command("entry-add")
@guarded
def entry_add(
    project_id: int,
    target_id: int = typer.Option(...),
    url: str = typer.Option(...),
    name: str = typer.Option(...),
):
    with session_scope() as session:
        emit(
            entry_view(
                session,
                projects.add_entry(
                    session,
                    project_id,
                    ProjectEntryCreate(target_id=target_id, entry_url=url, name=name),
                ),
            )
        )


@app.command("entry-update")
@guarded
def entry_update(
    project_id: int,
    entry_id: int,
    target_id: int = typer.Option(...),
    url: str = typer.Option(...),
    name: str = typer.Option(...),
    revision: int = typer.Option(...),
    enabled: bool = True,
):
    with session_scope() as session:
        projects.get_entry(session, project_id, entry_id)
        emit(
            entry_view(
                session,
                projects.update_entry(
                    session,
                    entry_id,
                    ProjectEntryCreate(
                        target_id=target_id, entry_url=url, name=name, enabled=enabled
                    ),
                    revision,
                ),
            )
        )


@app.command("import")
@guarded
def import_run(
    project_id: int,
    entry_id: int = typer.Option(...),
    source: str = typer.Option(...),
    run_id: int = typer.Option(...),
    yes: bool = typer.Option(False, "--yes"),
):
    service = ProjectIngestionService()
    with session_scope() as session:
        preview = service.preview(
            session,
            project_id,
            ProjectImportRequest(
                entry_id=entry_id, source_ref=ProjectSourceRef(source=source, run_id=run_id)
            ),
        )
    console.print(
        f"预览：{preview.subject_count} 个路由主体，{preview.observation_count} 条观察，"
        f"{preview.candidate_count} 条候选。",
        markup=False,
    )
    for note in preview.limitations:
        console.print(note, markup=False)
    if not yes and not typer.confirm("确认导入到项目资产库？"):
        return
    with session_scope() as session:
        emit(run_view(service.confirm(session, project_id, preview.token)))


@app.command("sync")
@guarded
def sync(project_id: int, project_run_id: int):
    with session_scope() as session:
        run = session.get(ProjectRun, project_run_id)
        if not run or run.project_id != project_id:
            raise ResourceNotFoundError("project_run_missing")
        emit(run_view(ProjectIngestionService().sync(session, project_run_id)))


def query_from(filters, **values):
    supplied = json.loads(filters)
    if not isinstance(supplied, dict) or set(supplied) - set(ProjectQuery.model_fields):
        raise InputValidationError("未知筛选字段。")
    return ProjectQuery(**(values | supplied))


@app.command("assets")
@guarded
def assets(
    project_id: int,
    view: str = "assets",
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    filters: str = typer.Option(
        "{}", help="JSON 筛选对象：site/kind/method/identity_key/tag/seen_from/seen_to 等。"
    ),
    as_json: bool = typer.Option(False, "--json"),
):
    with session_scope() as session:
        result = ProjectCatalogService().list(
            session, project_id, query_from(filters, view=view, q=q, page=page, page_size=page_size)
        )
    if as_json:
        emit(result)
        return
    console.print(
        f"匹配 {result.matched}；项目累计 {result.counts['assets']} 个主体，"
        f"{result.counts['observations']} 条观察。",
        markup=False,
    )
    table = Table("ID", "地址/来源", "观察数")
    for row in result.items:
        table.add_row(
            str(row["id"]),
            str(row.get("url") or row.get("record", {}).get("url") or row.get("source_key", "")),
            str(row.get("observation_count", "—")),
        )
    console.print(table)
    for note in result.limitations:
        console.print(note, markup=False)


@app.command("history")
@guarded
def history(project_id: int, asset_id: str, page: int = 1, page_size: int = 50):
    with session_scope() as session:
        emit(
            ProjectCatalogService().list(
                session,
                project_id,
                ProjectQuery(
                    view="observations", asset_id=asset_id, page=page, page_size=page_size
                ),
            )
        )


@app.command("relations")
@guarded
def relations(project_id: int, filters: str = "{}"):
    with session_scope() as session:
        emit(
            ProjectCatalogService().list(session, project_id, query_from(filters, view="relations"))
        )


@app.command("compare")
@guarded
def compare(project_id: int, before_run_id: int, after_run_id: int):
    with session_scope() as session:
        emit(ProjectComparisonService().compare(session, project_id, before_run_id, after_run_id))


@app.command("annotate")
@guarded
def annotate(
    project_id: int,
    asset_id: str,
    revision: int = typer.Option(...),
    tags: str = "[]",
    note: str = "",
):
    selected = json.loads(tags)
    if not isinstance(selected, list):
        raise InputValidationError("标签须为 JSON 数组。")
    with session_scope() as session:
        row = ProjectCurationService().annotate(
            session, project_id, asset_id, selected, note, revision
        )
        emit(dict(asset_id=row.asset_id, tags=row.tags, note=row.note))


@app.command("correct")
@guarded
def correct(
    project_id: int,
    action: str = typer.Option(...),
    asset_ids: str = typer.Option(...),
    reason: str = typer.Option(...),
    target_asset_id: str | None = None,
    observation_ids: str = "[]",
    yes: bool = typer.Option(False, "--yes"),
):
    service = ProjectCurationService()
    request = ProjectCorrectionRequest(
        action=action,
        asset_ids=json.loads(asset_ids),
        target_asset_id=target_asset_id,
        observation_ids=json.loads(observation_ids),
        reason=reason,
    )
    with session_scope() as session:
        preview = service.preview(session, project_id, request)
    console.print(
        f"整理预览：{preview.before_subject_count} → {preview.after_subject_count} 个主体，"
        f"影响 {preview.affected_observation_count} 条观察。",
        markup=False,
    )
    console.print(f"操作：{preview.action}；目标主体：{preview.target_asset_id}", markup=False)
    for assignment in preview.assignments:
        console.print(
            f"观察 #{assignment['observation_id']} · 项目运行 #{assignment['project_run_id']} · "
            f"{assignment['identity_key']} · {assignment['url']}\n"
            f"{assignment['from_asset_id']} → {assignment['to_asset_id']}",
            markup=False,
        )
    if not yes and not typer.confirm("确认执行可撤销整理？"):
        return
    with session_scope() as session:
        emit(correction_view(service.confirm(session, project_id, preview.token)))


@app.command("revoke")
@guarded
def revoke(
    project_id: int,
    correction_id: int,
    revision: int = typer.Option(...),
    reason: str = typer.Option(...),
):
    with session_scope() as session:
        emit(
            correction_view(
                ProjectCurationService().revoke(
                    session, project_id, correction_id, revision, reason
                )
            )
        )


@app.command("export")
@guarded
def export(
    project_id: int,
    format: str = "json",
    view: str = "assets",
    filters: str = "{}",
    output: Path | None = None,
):
    with session_scope() as session:
        content = export_project(session, project_id, query_from(filters, view=view), format)
    if output is None:
        typer.echo(content, nl=False)
    else:
        output.write_text(content, encoding="utf-8")
        console.print(f"已导出：{output}", markup=False)
