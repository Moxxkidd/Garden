"""Minimal server-rendered pages."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.settings import get_settings
from app.db.bootstrap import session_scope
from app.services.collection_presentation import COLLECTION_FIELDS
from app.services.dashboard import DashboardService
from app.services.scan_result_presentation import coverage_summary, format_execution_progress

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
templates.env.globals["collection_fields"] = COLLECTION_FIELDS
templates.env.globals.update(
    coverage_summary=coverage_summary,
    format_execution_progress=format_execution_progress,
)
router = APIRouter(tags=["pages"])
dashboard_service = DashboardService()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request) -> HTMLResponse:
    settings = get_settings()
    with session_scope() as session:
        dashboard = dashboard_service.build_summary(session)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "page_title": "概览",
            "dashboard": dashboard,
            "project_name": settings.project_name,
            "project_version": settings.project_version,
        },
    )


@router.get("/reports", response_class=HTMLResponse, include_in_schema=False)
def reports(request: Request):
    from sqlalchemy import select

    from app.models.scan_run import ScanRun

    with session_scope() as session:
        rows = [
            dict(id=r.id, url=r.normalized_url, status=r.status)
            for r in session.scalars(
                select(ScanRun)
                .where(ScanRun.report_path.is_not(None))
                .order_by(ScanRun.id.desc())
                .limit(50)
            )
        ]
    return templates.TemplateResponse(
        request=request, name="reports.html", context={"rows": rows, "page_title": "报告"}
    )


@router.get("/settings", response_class=HTMLResponse, include_in_schema=False)
def settings_page(request: Request):
    from app.schemas.scan import ScanOptions

    settings = get_settings()
    options = ScanOptions()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "page_title": "设置",
            "version": settings.project_version,
            "budgets": dict(pages=options.max_pages, resources=options.max_resources),
        },
    )
