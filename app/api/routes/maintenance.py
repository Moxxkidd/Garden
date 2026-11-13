"""Explicit adapters for local rechecks and private data operations."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from app.api.routes.project_pages import _HEADERS, form_values
from app.api.routes.projects import no_cache, payload
from app.core.errors import InputValidationError
from app.db.bootstrap import session_scope
from app.schemas.project_schedule import ScheduleRequest
from app.schemas.projects import ProjectRevision, ProjectView
from app.services.project_freshness import ProjectFreshnessService
from app.services.projects import ProjectService

router = APIRouter(tags=["maintenance"], dependencies=[Depends(no_cache)])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=100)
    enable: bool = False


class SchedulePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: ScheduleRequest
    schedule_id: int | None = Field(default=None, gt=0)


class FreshnessRequest(ProjectRevision):
    days: int = Field(ge=1, le=3650)


class BackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: str = Field(min_length=1, max_length=2000)


class RestoreRequest(BackupRequest):
    backup_directory: str = Field(min_length=1, max_length=2000)


class CleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retention_days: int = Field(default=30, ge=0, le=3650)
    archived_project_ids: list[int] = Field(default_factory=list, max_length=100)


def render(request, name, **context):
    return templates.TemplateResponse(
        request=request, name="projects/" + name + ".html", context=context, headers=_HEADERS
    )


@router.get("/api/projects/{project_id}/freshness")
def freshness(project_id: int):
    with session_scope() as session:
        return ProjectFreshnessService().view(session, project_id)


@router.post("/api/projects/{project_id}/freshness")
async def configure_freshness(project_id: int, request: Request):
    body = await payload(request, FreshnessRequest)
    with session_scope() as session:
        return ProjectView.model_validate(
            ProjectFreshnessService().configure(
                session, project_id, body.days, body.expected_revision
            )
        )


@router.get("/api/projects/{project_id}/schedules")
def schedules(project_id: int, request: Request):
    with session_scope() as session:
        return request.app.state.project_schedules.list(session, project_id)


@router.post("/api/projects/{project_id}/schedules/preview")
async def schedule_preview(project_id: int, request: Request):
    body = await payload(request, SchedulePreview)
    with session_scope() as session:
        return request.app.state.project_schedules.preview(
            session, project_id, body.request, schedule_id=body.schedule_id
        )


@router.post("/api/projects/{project_id}/schedules")
async def schedule_confirm(project_id: int, request: Request):
    body = await payload(request, Confirmation)
    with session_scope() as session:
        pid, *_ = request.app.state.project_schedules.tokens.get(body.token)
        if pid != project_id:
            raise InputValidationError("计划预览不属于当前项目。")
        return request.app.state.project_schedules.confirm(session, body.token, enable=body.enable)


@router.post("/api/projects/{project_id}/schedules/{schedule_id}/pause")
async def pause_schedule(project_id: int, schedule_id: int, request: Request):
    from app.api.routes.identities import check_origin

    check_origin(request)
    with session_scope() as session:
        return request.app.state.project_schedules.pause(session, project_id, schedule_id)


@router.get("/api/projects/{project_id}/schedules/{schedule_id}/history")
def schedule_history(project_id: int, schedule_id: int, request: Request):
    with session_scope() as session:
        return request.app.state.project_schedules.history(session, project_id, schedule_id)


@router.post("/api/projects/{project_id}/restore")
async def restore_project(project_id: int, request: Request):
    body = await payload(request, ProjectRevision)
    with session_scope() as session:
        return ProjectView.model_validate(
            ProjectService().restore(session, project_id, body.expected_revision)
        )


@router.get("/api/storage")
def storage_usage(request: Request):
    return request.app.state.local_data.usage()


@router.post("/api/storage/backups")
async def backup(request: Request):
    body = await payload(request, BackupRequest)
    return await run_in_threadpool(request.app.state.local_data.backup, body.destination)


@router.post("/api/storage/restore")
async def restore_backup(request: Request):
    body = await payload(request, RestoreRequest)
    return await run_in_threadpool(
        request.app.state.local_data.restore, body.backup_directory, body.destination
    )


@router.post("/api/storage/cleanup-preview")
async def cleanup_preview(request: Request):
    body = await payload(request, CleanupRequest)
    return request.app.state.local_data.cleanup_preview(**body.model_dump())


@router.post("/api/storage/cleanup")
async def cleanup(request: Request):
    body = await payload(request, Confirmation)
    return await run_in_threadpool(request.app.state.local_data.cleanup_confirm, body.token)


@router.get("/projects/{project_id}/maintenance", include_in_schema=False)
def maintenance_page(project_id: int, request: Request):
    from sqlalchemy import select

    from app.models.credential_profile import CredentialProfile

    with session_scope() as session:
        projects = ProjectService()
        project = ProjectView.model_validate(projects.get(session, project_id))
        entries = projects.entries(session, project_id)
        result = dict(
            project=project,
            entries=[dict(id=e.id, name=e.name) for e in entries],
            profiles=[
                dict(id=p.id, name=p.name, target_id=p.target_id)
                for p in session.scalars(
                    select(CredentialProfile).where(
                        CredentialProfile.target_id.in_({e.target_id for e in entries})
                    )
                )
            ],
            freshness=ProjectFreshnessService().view(session, project_id),
            schedules=request.app.state.project_schedules.list(session, project_id),
            history={
                r["id"]: request.app.state.project_schedules.history(session, project_id, r["id"])
                for r in request.app.state.project_schedules.list(session, project_id)
            },
        )
    return render(
        request,
        "maintenance",
        **result,
        start_at=(datetime.now(timezone.utc) + timedelta(hours=24))
        .replace(microsecond=0)
        .isoformat(),
        page_title="时效与周期复查",
    )


@router.post("/projects/{project_id}/freshness", include_in_schema=False)
async def freshness_form(project_id: int, request: Request):
    values = await form_values(request)
    try:
        body = FreshnessRequest.model_validate(values)
    except ValidationError:
        raise InputValidationError("新鲜度设置无效。") from None
    with session_scope() as session:
        ProjectFreshnessService().configure(session, project_id, body.days, body.expected_revision)
    return RedirectResponse(f"/projects/{project_id}/maintenance", status_code=303)


@router.post("/projects/{project_id}/schedules/preview", include_in_schema=False)
async def schedule_form_preview(project_id: int, request: Request):
    values = await form_values(request)
    try:
        body = ScheduleRequest(
            entry_id=values["entry_id"],
            profile_id=values.get("profile_id") or None,
            start_at=values["start_at"],
            interval_hours=values.get("interval_hours", 24),
            options={
                "max_pages": values.get("max_pages", 50),
                "durable": {"max_requests": values.get("max_requests", 1000)},
            },
        )
        sid = int(values["schedule_id"]) if values.get("schedule_id") else None
    except (ValidationError, KeyError, ValueError):
        raise InputValidationError(
            "计划参数无效，请检查带时区的开始时间、入口、身份与预算。"
        ) from None
    with session_scope() as session:
        preview = request.app.state.project_schedules.preview(
            session, project_id, body, schedule_id=sid
        )
    return render(
        request,
        "schedule_preview",
        preview=preview,
        project_id=project_id,
        page_title="确认自动请求范围",
    )


@router.post("/projects/{project_id}/schedules", include_in_schema=False)
async def schedule_form_confirm(project_id: int, request: Request):
    values = await form_values(request)
    with session_scope() as session:
        pid, *_ = request.app.state.project_schedules.tokens.get(values.get("token"))
        if pid != project_id:
            raise InputValidationError("计划不属于此项目。")
        request.app.state.project_schedules.confirm(
            session, values.get("token"), enable=values.get("enable") == "yes"
        )
    return RedirectResponse(f"/projects/{project_id}/maintenance", status_code=303)


@router.post("/projects/{project_id}/schedules/{schedule_id}/pause", include_in_schema=False)
async def schedule_form_pause(project_id: int, schedule_id: int, request: Request):
    await form_values(request)
    with session_scope() as session:
        request.app.state.project_schedules.pause(session, project_id, schedule_id)
    return RedirectResponse(f"/projects/{project_id}/maintenance", status_code=303)


@router.post("/projects/{project_id}/visibility", include_in_schema=False)
async def project_visibility(project_id: int, request: Request):
    values = await form_values(request)
    try:
        revision = int(values["expected_revision"])
    except (ValueError, KeyError):
        raise InputValidationError("项目修订无效。") from None
    with session_scope() as session:
        service = ProjectService()
        operation = service.restore if values.get("action") == "restore" else service.archive
        operation(session, project_id, revision)
    return RedirectResponse(f"/projects/{project_id}/maintenance", status_code=303)


@router.get("/storage", include_in_schema=False)
def storage_page(request: Request):
    with session_scope() as session:
        archives = [
            dict(id=p.id, name=p.name)
            for p in ProjectService().list(session)
            if p.status == "archived"
        ]
    return render(
        request,
        "storage",
        usage=request.app.state.local_data.usage(),
        archives=archives,
        page_title="本地数据管理",
    )


@router.post("/storage/{operation}", include_in_schema=False)
async def storage_form(operation: str, request: Request):
    values = await form_values(request)
    service = request.app.state.local_data
    if operation == "backup":
        result = await run_in_threadpool(service.backup, values.get("destination", ""))
    elif operation == "restore":
        result = await run_in_threadpool(
            service.restore, values.get("backup_directory", ""), values.get("destination", "")
        )
    elif operation == "cleanup-preview":
        try:
            body = CleanupRequest(
                retention_days=values.get("retention_days", 30),
                archived_project_ids=[
                    int(x) for x in values.get("archived_project_ids", "").split()
                ],
            )
        except (ValueError, ValidationError):
            raise InputValidationError("清理参数无效。") from None
        result = service.cleanup_preview(**body.model_dump())
    elif operation == "cleanup":
        result = await run_in_threadpool(service.cleanup_confirm, values.get("token"))
    else:
        raise InputValidationError("未知数据管理操作。")
    return render(
        request, "storage_result", operation=operation, result=result, page_title="本地数据操作结果"
    )
