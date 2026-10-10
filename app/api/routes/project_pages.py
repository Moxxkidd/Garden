"""Native project forms: explicit previews and confirmations, no JavaScript required."""

from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select

from app.api.routes.identities import check_origin
from app.api.routes.projects import parse_query
from app.core.errors import GardenError, InputValidationError, ResourceNotFoundError
from app.db.bootstrap import session_scope
from app.models.project import ProjectRun
from app.models.target import Target
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
from app.services.project_ingestion import ProjectIngestionService
from app.services.project_presentation import entry_view, run_view
from app.services.projects import ProjectService

router = APIRouter(tags=["projects"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
projects, catalog = ProjectService(), ProjectCatalogService()
ingestion, curation = ProjectIngestionService(), ProjectCurationService()
_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def render(request, template, **context):
    return templates.TemplateResponse(
        request=request, name=f"projects/{template}.html", context=context, headers=_HEADERS
    )


def redirect(project_id, suffix=""):
    return RedirectResponse(f"/projects/{project_id}{suffix}", status_code=303, headers=_HEADERS)


async def form_values(request):
    check_origin(request)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 512 * 1024:
            raise InputValidationError("项目表单超过大小限制。")
    try:
        return dict(parse_qsl(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=50))
    except (ValueError, UnicodeError):
        raise InputValidationError("项目表单格式无效。") from None


@router.get("/projects", include_in_schema=False)
def list_page(request: Request):
    with session_scope() as session:
        values = [ProjectView.model_validate(p) for p in projects.list(session)]
    return render(request, "list", projects=values, page_title="项目资产库")


@router.post("/projects/create", include_in_schema=False)
async def create(request: Request):
    values = await form_values(request)
    with session_scope() as session:
        project = projects.create(session, values.get("name", ""), values.get("description", ""))
        return redirect(project.id)


@router.get("/projects/{project_id}", include_in_schema=False)
def detail_page(request: Request, project_id: int):
    with session_scope() as session:
        project = ProjectView.model_validate(projects.get(session, project_id))
        entries = [entry_view(e) for e in projects.entries(session, project_id)]
        runs = [run_view(r) for r in ingestion.runs(session, project_id)]
        targets = [
            dict(id=t.id, name=t.name)
            for t in session.scalars(
                select(Target).where(Target.status == "active").order_by(Target.id)
            )
        ]
        counts = catalog.list(session, project_id, ProjectQuery(page_size=1)).counts
    return render(
        request,
        "detail",
        project=project,
        entries=entries,
        runs=runs,
        targets=targets,
        counts=counts,
        page_title=project.name,
    )


@router.get("/projects/{project_id}/assets", include_in_schema=False)
def assets_page(request: Request, project_id: int):
    query = parse_query(request.query_params)
    with session_scope() as session:
        project = ProjectView.model_validate(projects.get(session, project_id))
        result = catalog.list(session, project_id, query)

    def link(path, **changes):
        return path + "?" + urlencode(query.model_dump(mode="json", exclude_none=True) | changes)

    base = f"/projects/{project_id}/assets"
    return render(
        request,
        "assets",
        project=project,
        result=result,
        query=query,
        previous=link(base, page=query.page - 1) if query.page > 1 else None,
        next=link(base, page=query.page + 1)
        if query.page * query.page_size < result.matched
        else None,
        exports={
            f: link(f"/api/projects/{project_id}/export", format=f)
            for f in ("json", "csv", "markdown")
        },
        page_title="项目清单",
    )


@router.get("/projects/{project_id}/assets/{asset_id}", include_in_schema=False)
def asset_page(request: Request, project_id: int, asset_id: str):
    with session_scope() as session:
        project = ProjectView.model_validate(projects.get(session, project_id))
        detail = catalog.detail(session, project_id, asset_id)
    return render(request, "asset_detail", project=project, detail=detail, page_title="资产历史")


@router.get("/projects/{project_id}/compare", include_in_schema=False)
def compare_page(request: Request, project_id: int, before_run_id: int, after_run_id: int):
    with session_scope() as session:
        project = ProjectView.model_validate(projects.get(session, project_id))
        comparison = ProjectComparisonService().compare(
            session, project_id, before_run_id, after_run_id
        )
    return render(
        request, "comparison", project=project, comparison=comparison, page_title="项目变更"
    )


@router.post("/projects/{project_id}/{action:path}", include_in_schema=False)
async def mutate(request: Request, project_id: int, action: str):
    try:
        values = await form_values(request)
        with session_scope() as session:
            project = ProjectView.model_validate(projects.get(session, project_id))
            if action == "entries":
                projects.add_entry(session, project_id, ProjectEntryCreate(**values))
            elif action.startswith("entries/") and action.endswith("/update"):
                entry_id = int(action.split("/")[1])
                projects.get_entry(session, project_id, entry_id)
                projects.update_entry(
                    session,
                    entry_id,
                    ProjectEntryCreate(**values),
                    int(values["expected_revision"]),
                )
            elif action == "archive":
                projects.archive(session, project_id, int(values["expected_revision"]))
            elif action == "imports/preview":
                preview = ingestion.preview(
                    session,
                    project_id,
                    ProjectImportRequest(
                        entry_id=int(values["entry_id"]),
                        source_ref=ProjectSourceRef(
                            source=values["source"], run_id=int(values["run_id"])
                        ),
                    ),
                )
                return render(
                    request,
                    "preview",
                    project=project,
                    preview=preview,
                    action="imports/confirm",
                    label="确认导入",
                    page_title="历史入库预览",
                )
            elif action == "imports/confirm":
                ingestion.confirm(session, project_id, values["preview_token"])
                return redirect(project_id, "/assets")
            elif action.startswith("runs/") and action.endswith("/sync"):
                row = session.get(ProjectRun, int(action.split("/")[1]))
                if not row or row.project_id != project_id:
                    raise ResourceNotFoundError("project_run_missing")
                ingestion.sync(session, row.id)
            elif action == "annotations":
                curation.annotate(
                    session,
                    project_id,
                    values["asset_id"],
                    [v.strip() for v in values.get("tags", "").splitlines() if v.strip()],
                    values.get("note", ""),
                    int(values["expected_revision"]),
                )
                return redirect(project_id, "/assets/" + values["asset_id"])
            elif action == "corrections/preview":
                preview = curation.preview(
                    session,
                    project_id,
                    ProjectCorrectionRequest(
                        action=values["action"],
                        asset_ids=values["asset_ids"].replace(",", " ").split(),
                        target_asset_id=values.get("target_asset_id") or None,
                        observation_ids=[
                            int(v)
                            for v in values.get("observation_ids", "").replace(",", " ").split()
                        ],
                        reason=values["reason"],
                    ),
                )
                return render(
                    request,
                    "preview",
                    project=project,
                    preview=preview,
                    action="corrections/confirm",
                    label="确认整理",
                    page_title="人工整理预览",
                )
            elif action == "corrections/confirm":
                curation.confirm(session, project_id, values["preview_token"])
                return redirect(project_id, "/assets")
            elif action.startswith("corrections/") and action.endswith("/revoke"):
                curation.revoke(
                    session,
                    project_id,
                    int(action.split("/")[1]),
                    int(values["expected_revision"]),
                    values["reason"],
                )
                return redirect(project_id, "/assets")
            else:
                raise ResourceNotFoundError("project_action_missing")
        return redirect(project_id)
    except (ValidationError, ValueError, KeyError):
        return render(
            request,
            "error",
            project_id=project_id,
            message="字段无效，请返回检查必填项与取值范围。",
            page_title="请检查输入",
        )
    except GardenError as error:
        return render(
            request, "error", project_id=project_id, message=str(error), page_title="操作未完成"
        )


@router.get("/projects/{project_id}/entries/{entry_id}/scan", include_in_schema=False)
def scan_entry(request: Request, project_id: int, entry_id: int):
    from app.api.routes.scan_preview_pages import _render

    with session_scope() as session:
        entry = projects.validate_entry(session, project_id, entry_id)
        values = dict(
            url=entry.entry_url, project_entry_id=str(entry.id), target_id=str(entry.target_id)
        )
    return _render(request, values)
