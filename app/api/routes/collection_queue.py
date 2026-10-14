"""Bounded adapters for durable work; executable materials remain private."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes.projects import no_cache, payload
from app.core.errors import ResourceNotFoundError
from app.db.bootstrap import session_scope
from app.models.scan_run import ScanRun
from app.schemas.collection_resume import CollectionResumeRequest
from app.schemas.project_incremental import ProjectIncrementalRequest
from app.services.collection_queue import CollectionQueueService
from app.services.collection_resume import CollectionResumeService
from app.services.project_incremental import ProjectIncrementalService

router = APIRouter(tags=["collection"], dependencies=[Depends(no_cache)])
queues = CollectionQueueService()


class QueueSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queue_id: int = Field(gt=0)


class ResumeConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: CollectionResumeRequest
    token: str = Field(min_length=64, max_length=64)


def check_run(session, run_id, queue_id=None):
    if session.get(ScanRun, run_id) is None:
        raise ResourceNotFoundError("scan_not_found")
    if queue_id is not None and queues.row(session, queue_id).run_id != run_id:
        raise ResourceNotFoundError("collection_queue_missing")


def resume_service(request):
    return CollectionResumeService(
        scans=request.app.state.scan_service,
        identities=getattr(request.app.state, "identity_collection", None),
    )


@router.get("/api/scans/{run_id}/queue")
def show(run_id: int):
    with session_scope() as session:
        check_run(session, run_id)
        return queues.for_run(session, run_id)


@router.get("/api/scans/{run_id}/queue/{queue_id}/work")
def work(run_id: int, queue_id: int, state: str | None = None, page: int = 1, page_size: int = 50):
    with session_scope() as session:
        check_run(session, run_id, queue_id)
        return queues.list_work(session, queue_id, state=state, page=page, page_size=page_size)


@router.post("/api/scans/{run_id}/pause")
async def pause(run_id: int, request: Request):
    body = await payload(request, QueueSelection)
    with session_scope() as session:
        check_run(session, run_id, body.queue_id)
        return queues.pause(session, body.queue_id)


@router.post("/api/scans/{run_id}/resume-preview")
async def preview(run_id: int, request: Request):
    body = await payload(request, CollectionResumeRequest)
    with session_scope() as session:
        check_run(session, run_id, body.queue_id)
        return resume_service(request).preview(session, body)


@router.post("/api/scans/{run_id}/resume")
async def resume(run_id: int, request: Request):
    body = await payload(request, ResumeConfirmation)
    with session_scope() as session:
        check_run(session, run_id, body.request.queue_id)
        return {"run_id": resume_service(request).start(session, body.request, body.token)}


class IncrementalConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: ProjectIncrementalRequest
    token: str = Field(min_length=1, max_length=100)


def incremental_service(request):
    if not hasattr(request.app.state, "project_incremental"):
        request.app.state.project_incremental = ProjectIncrementalService(
            scans=request.app.state.scan_service,
            identities=getattr(request.app.state, "identity_collection", None),
        )
    return request.app.state.project_incremental


def check_entry(project_id, entry_id, body):
    if body.project_id != project_id or body.entry_id != entry_id:
        from app.core.errors import InputValidationError

        raise InputValidationError("project_incremental_entry_mismatch")


@router.post("/api/projects/{project_id}/entries/{entry_id}/incremental-preview")
async def incremental_preview(project_id: int, entry_id: int, request: Request):
    body = await payload(request, ProjectIncrementalRequest)
    check_entry(project_id, entry_id, body)
    with session_scope() as session:
        return incremental_service(request).preview(session, body)


@router.post("/api/projects/{project_id}/entries/{entry_id}/incremental")
async def incremental_start(project_id: int, entry_id: int, request: Request):
    body = await payload(request, IncrementalConfirmation)
    check_entry(project_id, entry_id, body.request)
    with session_scope() as session:
        return {"run_id": incremental_service(request).start(session, body.request, body.token)}


@router.get("/api/scans/{run_id}/queue/{queue_id}/export")
def export(run_id: int, queue_id: int, format: str = "json", state: str | None = None):
    from fastapi.responses import Response

    from app.services.collection_export import export_collection

    with session_scope() as session:
        check_run(session, run_id, queue_id)
        content = export_collection(session, queue_id, format, state=state)
    return Response(
        content,
        media_type={"json": "application/json", "csv": "text/csv", "markdown": "text/markdown"}[
            format
        ],
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Disposition": f'attachment; filename="garden-queue-{queue_id}.{format}"',
        },
    )
