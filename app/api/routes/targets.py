"""Server-rendered target pages."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.bootstrap import session_scope
from app.services.sessions import AuthSessionService
from app.services.targets import TargetService

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
router = APIRouter(tags=["targets"])
service = TargetService()
session_service = AuthSessionService()


@router.get("/targets", response_class=HTMLResponse, include_in_schema=False)
def targets_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        targets = service.list(session)
    return templates.TemplateResponse(
        request=request,
        name="targets_list.html",
        context={"targets": targets, "page_title": "Targets"},
    )


@router.get("/targets/{target_id}", response_class=HTMLResponse, include_in_schema=False)
def target_detail_page(request: Request, target_id: int) -> HTMLResponse:
    with session_scope() as session:
        target = service.get(session, target_id)
        auth_sessions = session_service.list_for_target(session, target_id)
    return templates.TemplateResponse(
        request=request,
        name="target_detail.html",
        context={
            "target": target,
            "auth_sessions": auth_sessions,
            "page_title": f"Target {target.id}",
        },
    )
