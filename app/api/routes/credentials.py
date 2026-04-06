"""Server-rendered credential pages."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.bootstrap import session_scope
from app.redaction.display import redact_secret_ref
from app.services.credentials import CredentialProfileService
from app.services.sessions import AuthSessionService

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
router = APIRouter(tags=["credentials"])
service = CredentialProfileService()
session_service = AuthSessionService()


@router.get("/credentials", response_class=HTMLResponse, include_in_schema=False)
def credentials_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        credentials = service.list(session)
    return templates.TemplateResponse(
        request=request,
        name="credentials_list.html",
        context={"credentials": credentials, "page_title": "Credential Profiles"},
    )


@router.get("/credentials/{credential_id}", response_class=HTMLResponse, include_in_schema=False)
def credential_detail_page(request: Request, credential_id: int) -> HTMLResponse:
    with session_scope() as session:
        credential = service.get(session, credential_id)
        auth_sessions = session_service.list_for_credential(session, credential_id)
    return templates.TemplateResponse(
        request=request,
        name="credential_detail.html",
        context={
            "credential": credential,
            "credential_secret_ref_redacted": redact_secret_ref(credential.secret_ref),
            "auth_sessions": auth_sessions,
            "page_title": f"Credential {credential.id}",
        },
    )
