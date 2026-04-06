"""Server-rendered evidence pages."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.bootstrap import session_scope
from app.services.evidence import EvidenceService

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
router = APIRouter(tags=["evidence"])
service = EvidenceService()


@router.get("/evidence", response_class=HTMLResponse, include_in_schema=False)
def evidence_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        evidences = service.list(session)
    return templates.TemplateResponse(
        request=request,
        name="evidence_list.html",
        context={"evidences": evidences, "page_title": "Evidence"},
    )


@router.get("/evidence/{evidence_id}", response_class=HTMLResponse, include_in_schema=False)
def evidence_detail_page(request: Request, evidence_id: int) -> HTMLResponse:
    with session_scope() as session:
        evidence = service.get(session, evidence_id)
        payload = service.payload_for(evidence)
    return templates.TemplateResponse(
        request=request,
        name="evidence_detail.html",
        context={
            "evidence": evidence,
            "payload": payload,
            "page_title": f"Evidence {evidence.id}",
        },
    )
