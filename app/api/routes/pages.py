"""Minimal server-rendered pages."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.settings import get_settings
from app.db.bootstrap import session_scope
from app.services.dashboard import DashboardService

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
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
            "page_title": "Dashboard",
            "dashboard": dashboard,
            "project_name": settings.project_name,
            "project_version": settings.project_version,
        },
    )
