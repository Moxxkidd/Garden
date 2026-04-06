"""Server-rendered scan job pages."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.bootstrap import session_scope
from app.services.inventory import InventoryBuildService
from app.services.jobs import ScanJobService

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
router = APIRouter(tags=["jobs"])
service = ScanJobService()
inventory_service = InventoryBuildService()


@router.get("/jobs", response_class=HTMLResponse, include_in_schema=False)
def jobs_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        jobs = service.list(session)
    return templates.TemplateResponse(
        request=request,
        name="jobs_list.html",
        context={"jobs": jobs, "page_title": "Scan Jobs"},
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
def job_detail_page(request: Request, job_id: int) -> HTMLResponse:
    with session_scope() as session:
        job = service.get(session, job_id)
        inventory_run = inventory_service.get_by_job(session, job_id)
    return templates.TemplateResponse(
        request=request,
        name="job_detail.html",
        context={"job": job, "inventory_run": inventory_run, "page_title": f"Job {job.id}"},
    )
