"""Thin HTTP and server-rendered adapters for the core URL scan service."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.errors import ConflictError, GardenError, ResourceNotFoundError
from app.schemas.scan import ScanOptions, ScanRunView, ScanStartRequest
from app.schemas.scan_comparison import ScanComparison
from app.services.coverage_gaps import COVERAGE_GAP_NOTE
from app.services.scan_result_presentation import (
    coverage_summary,
    diagnostic_hint,
    execution_summary,
    format_execution_progress,
    summarize_scan_view,
)

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
templates.env.globals.update(
    coverage_gap_note=COVERAGE_GAP_NOTE,
    coverage_summary=coverage_summary,
    format_execution_progress=format_execution_progress,
)
router = APIRouter(tags=["scans"])


@router.post("/api/scans", response_model=ScanRunView, status_code=status.HTTP_202_ACCEPTED)
def start_scan_api(request: Request, payload: ScanStartRequest) -> ScanRunView:
    return request.app.state.scan_service.start_scan(payload.url, payload.options)


@router.get("/api/scans/{scan_run_id}", response_model=ScanRunView)
def scan_status_api(request: Request, scan_run_id: int) -> ScanRunView:
    return request.app.state.scan_service.get_scan(scan_run_id)


@router.post("/api/scans/{scan_run_id}/cancel", response_model=ScanRunView)
def cancel_scan_api(request: Request, scan_run_id: int) -> ScanRunView:
    return request.app.state.scan_service.cancel_scan(scan_run_id)


@router.get("/api/scans/{scan_run_id}/report")
def scan_report_api(
    request: Request,
    scan_run_id: int,
    download: bool = Query(default=False),
):
    view = request.app.state.scan_service.get_scan(scan_run_id)
    if download:
        request.app.state.scan_service.read_report(scan_run_id)
        return FileResponse(
            view.report_path,
            media_type="text/markdown; charset=utf-8",
            filename=f"garden-scan-{scan_run_id}.md",
        )
    return PlainTextResponse(
        request.app.state.scan_service.read_report(scan_run_id),
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/scans", response_class=HTMLResponse, include_in_schema=False)
def scans_page(request: Request) -> HTMLResponse:
    scans = request.app.state.scan_service.list_scans()
    return templates.TemplateResponse(
        request=request,
        name="scans_list.html",
        context={"scans": scans, "page_title": "URL Scans"},
    )


@router.post("/scans", include_in_schema=False)
def start_scan_page(
    request: Request,
    url: str = Form(...),
    max_pages: int = Form(default=50, ge=1, le=500),
    max_resources: int = Form(default=200, ge=0, le=2000),
    max_depth: int = Form(default=2, ge=0, le=5),
) -> RedirectResponse:
    scan = request.app.state.scan_service.start_scan(
        url,
        ScanOptions(
            max_pages=max_pages,
            max_resources=max_resources,
            max_depth=max_depth,
        ),
    )
    return RedirectResponse(url=f"/scans/{scan.id}", status_code=303)


@router.get("/scans/{scan_run_id}", response_class=HTMLResponse, include_in_schema=False)
def scan_detail_page(request: Request, scan_run_id: int) -> HTMLResponse:
    scan = request.app.state.scan_service.get_scan(scan_run_id)
    report = None
    if scan.report_path:
        report = request.app.state.scan_service.read_report(scan_run_id)
    return templates.TemplateResponse(
        request=request,
        name="scan_detail.html",
        context={
            "scan": scan,
            "report": report,
            "page_title": f"Scan {scan.id}",
            "execution_text": execution_summary(scan.status),
            "result_summary": summarize_scan_view(scan),
            "context_diagnostics": [
                {
                    "kind": context.kind.value,
                    "code": context.error_code,
                    "hint": diagnostic_hint("context", context.error_code),
                }
                for context in scan.contexts
                if context.error_code
            ],
            "diagnostic_hints": {
                (failure.stage, failure.code): diagnostic_hint(failure.stage, failure.code)
                for failure in scan.failures
            },
        },
    )


@router.get("/api/scans/{scan_run_id}/comparison", response_model=ScanComparison)
def scan_comparison_api(request: Request, response: Response, scan_run_id: int) -> ScanComparison:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return request.app.state.scan_service.compare_with_source(scan_run_id)


@router.get("/scans/{scan_run_id}/comparison", response_class=HTMLResponse, include_in_schema=False)
def scan_comparison_page(request: Request, scan_run_id: int) -> HTMLResponse:
    comparison, error, status_code = None, None, 200
    try:
        comparison = request.app.state.scan_service.compare_with_source(scan_run_id)
    except GardenError as exc:
        error = str(exc)
        status_code = (
            404
            if isinstance(exc, ResourceNotFoundError)
            else 409
            if isinstance(exc, ConflictError)
            else 400
        )
    return templates.TemplateResponse(
        request=request,
        name="scan_comparison.html",
        context={
            "comparison": comparison,
            "error": error,
            "scan_run_id": scan_run_id,
            "page_title": "与上次对比",
        },
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
