"""Thin HTTP and server-rendered adapters for the core URL scan service."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.schemas.scan import ScanOptions, ScanRunView, ScanStartRequest

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
router = APIRouter(tags=["scans"])


@router.post("/api/scans", response_model=ScanRunView, status_code=status.HTTP_202_ACCEPTED)
def start_scan_api(request: Request, payload: ScanStartRequest) -> ScanRunView:
    return request.app.state.scan_service.start_scan(payload.url, payload.options)


@router.get("/api/scans/{scan_run_id}", response_model=ScanRunView)
def scan_status_api(request: Request, scan_run_id: int) -> ScanRunView:
    return request.app.state.scan_service.get_scan(scan_run_id)


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
    max_pages: int = Form(default=10, ge=1, le=100),
    max_depth: int = Form(default=2, ge=0, le=5),
) -> RedirectResponse:
    scan = request.app.state.scan_service.start_scan(
        url,
        ScanOptions(max_pages=max_pages, max_depth=max_depth),
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
        context={"scan": scan, "report": report, "page_title": f"Scan {scan.id}"},
    )
