"""Bounded project adapters; all product rules live in shared services."""

from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError

from app.api.routes.identities import check_origin
from app.core.errors import InputValidationError, ResourceNotFoundError
from app.db.bootstrap import session_scope
from app.models.project import ProjectRun
from app.schemas.projects import (
    ProjectAnnotationRequest,
    ProjectConfirmation,
    ProjectCorrectionRequest,
    ProjectCreate,
    ProjectEntryCreate,
    ProjectEntryUpdate,
    ProjectImportRequest,
    ProjectQuery,
    ProjectRevision,
    ProjectRevokeRequest,
    ProjectView,
)
from app.services.project_catalog import ProjectCatalogService
from app.services.project_comparison import ProjectComparisonService
from app.services.project_curation import ProjectCurationService
from app.services.project_export import export_project
from app.services.project_ingestion import ProjectIngestionService
from app.services.project_presentation import correction_view, entry_view, run_view
from app.services.projects import ProjectService

_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def no_cache(response: Response):
    response.headers.update(_HEADERS)


router = APIRouter(prefix="/api/projects", tags=["projects"], dependencies=[Depends(no_cache)])
projects = ProjectService()
ingestion = ProjectIngestionService()
curation = ProjectCurationService()
catalog = ProjectCatalogService()


async def payload(request, schema):
    check_origin(request)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 512 * 1024:
            raise InputValidationError("项目请求超过大小限制。")
    try:
        return schema.model_validate_json(raw)
    except (ValidationError, ValueError):
        raise InputValidationError("项目请求格式无效，请检查字段与输入大小。") from None


def parse_query(values):
    values = {k: v for k, v in dict(values).items() if v != "" and k != "format"}
    if set(values) - set(ProjectQuery.model_fields):
        raise InputValidationError("项目查询包含未知筛选字段。")
    try:
        return ProjectQuery(**values)
    except (ValidationError, ValueError):
        raise InputValidationError("项目查询参数无效。") from None


@router.get("")
def list_projects():
    with session_scope() as session:
        return [ProjectView.model_validate(p) for p in projects.list(session)]


@router.post("")
async def create_project(request: Request):
    body = await payload(request, ProjectCreate)
    with session_scope() as session:
        return ProjectView.model_validate(projects.create(session, body.name, body.description))


@router.get("/{project_id}")
def show_project(project_id: int):
    with session_scope() as session:
        return dict(
            project=ProjectView.model_validate(projects.get(session, project_id)),
            entries=[entry_view(session, e) for e in projects.entries(session, project_id)],
            runs=[run_view(r) for r in ingestion.runs(session, project_id)],
        )


@router.post("/{project_id}/archive")
async def archive_project(project_id: int, request: Request):
    body = await payload(request, ProjectRevision)
    with session_scope() as session:
        return ProjectView.model_validate(
            projects.archive(session, project_id, body.expected_revision)
        )


@router.post("/{project_id}/entries")
async def add_entry(project_id: int, request: Request):
    body = await payload(request, ProjectEntryCreate)
    with session_scope() as session:
        return entry_view(session, projects.add_entry(session, project_id, body))


@router.post("/{project_id}/entries/{entry_id}")
async def update_entry(project_id: int, entry_id: int, request: Request):
    body = await payload(request, ProjectEntryUpdate)
    with session_scope() as session:
        projects.get_entry(session, project_id, entry_id)
        return entry_view(
            session, projects.update_entry(session, entry_id, body, body.expected_revision)
        )


@router.post("/{project_id}/imports/preview")
async def import_preview(project_id: int, request: Request):
    body = await payload(request, ProjectImportRequest)
    with session_scope() as session:
        return ingestion.preview(session, project_id, body)


@router.post("/{project_id}/imports")
async def import_confirm(project_id: int, request: Request):
    body = await payload(request, ProjectConfirmation)
    with session_scope() as session:
        return run_view(ingestion.confirm(session, project_id, body.preview_token))


@router.post("/{project_id}/runs/{project_run_id}/sync")
def sync_run(project_id: int, project_run_id: int, request: Request):
    check_origin(request)
    with session_scope() as session:
        row = session.get(ProjectRun, project_run_id)
        if not row or row.project_id != project_id:
            raise ResourceNotFoundError("project_run_missing")
        return run_view(ingestion.sync(session, project_run_id))


@router.get("/{project_id}/assets")
def list_assets(project_id: int, request: Request):
    with session_scope() as session:
        return catalog.list(session, project_id, parse_query(request.query_params))


@router.get("/{project_id}/assets/{asset_id}")
def asset_detail(project_id: int, asset_id: str):
    with session_scope() as session:
        return catalog.detail(session, project_id, asset_id)


@router.get("/{project_id}/compare")
def compare(project_id: int, before_run_id: int, after_run_id: int):
    with session_scope() as session:
        return ProjectComparisonService().compare(session, project_id, before_run_id, after_run_id)


@router.get("/{project_id}/export")
def export(project_id: int, request: Request, format: str = "json"):
    if format not in {"json", "csv", "markdown"}:
        raise InputValidationError("导出格式须为 json、csv 或 markdown。")
    with session_scope() as session:
        content = export_project(session, project_id, parse_query(request.query_params), format)
    return Response(
        content,
        media_type={"json": "application/json", "csv": "text/csv", "markdown": "text/markdown"}[
            format
        ],
        headers={
            **_HEADERS,
            "Content-Disposition": f'attachment; filename="garden-project-{project_id}.{format}"',
        },
    )


@router.post("/{project_id}/annotations")
async def annotate(project_id: int, request: Request):
    body = await payload(request, ProjectAnnotationRequest)
    with session_scope() as session:
        result = curation.annotate(
            session, project_id, body.asset_id, body.tags, body.note, body.expected_revision
        )
        return dict(asset_id=result.asset_id, tags=result.tags, note=result.note)


@router.post("/{project_id}/corrections/preview")
async def correction_preview(project_id: int, request: Request):
    body = await payload(request, ProjectCorrectionRequest)
    with session_scope() as session:
        return curation.preview(session, project_id, body)


@router.post("/{project_id}/corrections")
async def correction_confirm(project_id: int, request: Request):
    body = await payload(request, ProjectConfirmation)
    with session_scope() as session:
        return correction_view(curation.confirm(session, project_id, body.preview_token))


@router.post("/{project_id}/corrections/{correction_id}/revoke")
async def revoke(project_id: int, correction_id: int, request: Request):
    body = await payload(request, ProjectRevokeRequest)
    with session_scope() as session:
        return correction_view(
            curation.revoke(session, project_id, correction_id, body.expected_revision, body.reason)
        )
