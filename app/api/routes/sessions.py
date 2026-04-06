"""Server-rendered auth session pages."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.bootstrap import session_scope
from app.services.credentials import CredentialProfileService
from app.services.sessions import AuthSessionService
from app.services.targets import TargetService

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
router = APIRouter(tags=["sessions"])
session_service = AuthSessionService()
target_service = TargetService()
credential_service = CredentialProfileService()


@router.get("/sessions", response_class=HTMLResponse, include_in_schema=False)
def sessions_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        sessions = session_service.list(session)
        session_rows = [
            {
                "session": auth_session,
                "target_name": target_service.get(session, auth_session.target_id).name,
                "profile_name": credential_service.get(
                    session, auth_session.credential_profile_id
                ).name,
            }
            for auth_session in sessions
        ]
    return templates.TemplateResponse(
        request=request,
        name="sessions_list.html",
        context={"sessions": session_rows, "page_title": "Sessions"},
    )


@router.get("/sessions/{session_id}", response_class=HTMLResponse, include_in_schema=False)
def session_detail_page(request: Request, session_id: int) -> HTMLResponse:
    with session_scope() as session:
        auth_session = session_service.get(session, session_id)
        target = target_service.get(session, auth_session.target_id)
        credential = credential_service.get(session, auth_session.credential_profile_id)
    return templates.TemplateResponse(
        request=request,
        name="session_detail.html",
        context={
            "auth_session": auth_session,
            "target_name": target.name,
            "profile_name": credential.name,
            "session_metadata_json": json.dumps(auth_session.session_metadata_redacted, indent=2),
            "page_title": f"Session {auth_session.id}",
        },
    )
