"""Server-rendered findings pages."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db.bootstrap import session_scope
from app.services.evidence import EvidenceService
from app.services.findings import FindingService
from app.services.retest import RetestService

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
router = APIRouter(tags=["findings"])
service = FindingService()
evidence_service = EvidenceService()
retest_service = RetestService()


@router.get("/findings", response_class=HTMLResponse, include_in_schema=False)
def findings_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        findings = service.list(session)
        breakdown = service.status_breakdown(session)
    return templates.TemplateResponse(
        request=request,
        name="findings_list.html",
        context={"findings": findings, "breakdown": breakdown, "page_title": "Findings"},
    )


@router.get("/findings/{finding_id}", response_class=HTMLResponse, include_in_schema=False)
def finding_detail_page(request: Request, finding_id: int) -> HTMLResponse:
    with session_scope() as session:
        finding = service.get(session, finding_id)
        evidences = evidence_service.list(session, finding_id=finding_id)
        latest_retest = max(finding.retests, key=lambda item: item.id) if finding.retests else None
    return templates.TemplateResponse(
        request=request,
        name="finding_detail.html",
        context={
            "finding": finding,
            "evidences": evidences,
            "latest_retest": latest_retest,
            "allowed_statuses": [status.value for status in service.list_statuses()],
            "page_title": f"Finding {finding.id}",
        },
    )


@router.post("/findings/{finding_id}/status", include_in_schema=False)
def update_finding_status(finding_id: int, status: str = Form(...)) -> RedirectResponse:
    with session_scope() as session:
        service.update_status(session, finding_id, status)
    return RedirectResponse(url=f"/findings/{finding_id}", status_code=303)


@router.post("/findings/{finding_id}/retest", include_in_schema=False)
def retest_finding(finding_id: int) -> RedirectResponse:
    with session_scope() as session:
        retest_service.run(session, finding_id=finding_id)
    return RedirectResponse(url=f"/findings/{finding_id}", status_code=303)
