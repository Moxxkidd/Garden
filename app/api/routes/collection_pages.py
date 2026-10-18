"""Native queue forms with an explicit preview and confirmation step."""

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.api.routes.collection_queue import check_run, queues, resume_service
from app.api.routes.project_pages import form_values
from app.api.routes.projects import _HEADERS
from app.core.errors import InputValidationError
from app.db.bootstrap import session_scope
from app.schemas.collection_queue import CollectionOptions
from app.schemas.collection_resume import CollectionResumeRequest
from app.services.collection_presentation import (
    OPTION_LABELS,
    STATE_LABELS,
    collection_metric_lines,
)

router = APIRouter(tags=["collection"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))


def render(request, name, **context):
    context.update(
        option_labels=OPTION_LABELS, state_labels=STATE_LABELS, metric_lines=collection_metric_lines
    )
    return templates.TemplateResponse(
        request=request, name=f"collection/{name}.html", context=context, headers=_HEADERS
    )


@router.get("/scans/{run_id}/queue", include_in_schema=False)
def show(request: Request, run_id: int, page: int = 1, state: str | None = None):
    with session_scope() as session:
        check_run(session, run_id)
        values = queues.for_run(session, run_id)
        work = {q["id"]: queues.list_work(session, q["id"], page=page, state=state) for q in values}
    return render(
        request,
        "detail",
        run_id=run_id,
        queues=values,
        work=work,
        defaults=CollectionOptions().model_dump(),
        page=page,
        state=state or "",
        page_title="持久采集队列",
    )


@router.post("/scans/{run_id}/queue/preview", include_in_schema=False)
async def preview(request: Request, run_id: int):
    values = await form_values(request)
    try:
        body = CollectionResumeRequest(
            queue_id=values.get("queue_id"),
            work_ids=values.get("work_ids", "").split() or None,
            mode=values.get("mode", "known_only"),
            options={k: v for k, v in values.items() if k in CollectionOptions.model_fields},
        )
    except ValidationError:
        raise InputValidationError("队列参数无效，请检查预算和选择范围。") from None
    with session_scope() as session:
        check_run(session, run_id, body.queue_id)
        result = resume_service(request).preview(session, body)
    return render(
        request,
        "preview",
        run_id=run_id,
        preview=result,
        request_json=body.model_dump_json(),
        page_title="确认新轮补采",
    )


@router.post("/scans/{run_id}/queue/resume", include_in_schema=False)
async def resume(request: Request, run_id: int):
    values = await form_values(request)
    try:
        body = CollectionResumeRequest.model_validate(json.loads(values.get("request", "")))
    except (ValidationError, ValueError):
        raise InputValidationError("确认内容无效，请重新预览。") from None
    with session_scope() as session:
        check_run(session, run_id, body.queue_id)
        child = resume_service(request).start(session, body, values.get("token"))
    return RedirectResponse(f"/scans/{child}/queue", status_code=303, headers=_HEADERS)


@router.post("/scans/{run_id}/queue/pause", include_in_schema=False)
async def pause(request: Request, run_id: int):
    values = await form_values(request)
    try:
        qid = int(values.get("queue_id", ""))
    except ValueError:
        raise InputValidationError("请选择队列。") from None
    with session_scope() as session:
        check_run(session, run_id, qid)
        queues.pause(session, qid)
    return RedirectResponse(f"/scans/{run_id}/queue", status_code=303, headers=_HEADERS)


@router.get("/projects/{project_id}/entries/{entry_id}/incremental", include_in_schema=False)
def incremental_form(request: Request, project_id: int, entry_id: int):
    from sqlalchemy import select

    from app.models.credential_profile import CredentialProfile
    from app.services.projects import ProjectService

    with session_scope() as session:
        entry = ProjectService().validate_entry(session, project_id, entry_id)
        profiles = [
            {"id": p.id, "name": p.name}
            for p in session.scalars(
                select(CredentialProfile).where(CredentialProfile.target_id == entry.target_id)
            )
        ]
    return render(
        request,
        "incremental",
        project_id=project_id,
        entry_id=entry_id,
        profiles=profiles,
        defaults=CollectionOptions().model_dump(),
        page_title="项目增量计划",
    )


@router.post(
    "/projects/{project_id}/entries/{entry_id}/incremental-preview", include_in_schema=False
)
async def incremental_preview(request: Request, project_id: int, entry_id: int):
    from app.api.routes.collection_queue import incremental_service
    from app.schemas.project_incremental import ProjectIncrementalRequest

    values = await form_values(request)
    try:
        body = ProjectIncrementalRequest(
            project_id=project_id,
            entry_id=entry_id,
            profile_id=values.get("profile_id") or None,
            stale_after_days=values.get("stale_after_days", 30),
            important_asset_ids=values.get("important_asset_ids", "").split(),
            mode=values.get("mode", "known_only"),
            options={k: v for k, v in values.items() if k in CollectionOptions.model_fields},
        )
    except ValidationError:
        raise InputValidationError("增量计划参数无效，请检查身份与预算。") from None
    with session_scope() as session:
        result = incremental_service(request).preview(session, body)
    return render(
        request,
        "incremental_preview",
        project_id=project_id,
        entry_id=entry_id,
        preview=result,
        request_json=body.model_dump_json(),
        page_title="确认增量采集",
    )


@router.post("/projects/{project_id}/entries/{entry_id}/incremental", include_in_schema=False)
async def incremental_confirm(request: Request, project_id: int, entry_id: int):
    from app.api.routes.collection_queue import check_entry, incremental_service
    from app.schemas.project_incremental import ProjectIncrementalRequest

    values = await form_values(request)
    try:
        body = ProjectIncrementalRequest.model_validate_json(values.get("request", ""))
    except ValidationError:
        raise InputValidationError("确认内容无效，请重新预览。") from None
    check_entry(project_id, entry_id, body)
    with session_scope() as session:
        child = incremental_service(request).start(session, body, values.get("token"))
    return RedirectResponse(f"/scans/{child}/queue", status_code=303, headers=_HEADERS)
