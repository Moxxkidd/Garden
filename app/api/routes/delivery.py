"""Thin API and native Web adapters for versioned project delivery."""

import json
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.background import BackgroundTask

from app.api.routes.projects import parse_query
from app.core.errors import InputValidationError
from app.db.bootstrap import session_scope
from app.schemas.delivery import DeliveryDocument, DeliveryExportRequest
from app.services.asset_delivery import AssetDeliveryService
from app.services.delivery_report import summary_lines

router = APIRouter(tags=["delivery"])
service = AssetDeliveryService()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
MEDIA = {
    "json": "application/json",
    "csv": "text/csv",
    "markdown": "text/markdown",
    "html": "text/html",
    "openapi": "application/json",
    "readable_csv": "text/csv",
}


def delivery_query(request):
    return parse_query(
        {
            k: v
            for k, v in request.query_params.items()
            if k not in {"dataset", "cursor", "before_run_id", "after_run_id"}
        }
    )


@router.get("/api/delivery/schema")
def schema():
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **DeliveryDocument.model_json_schema(),
    }


@router.get("/api/projects/{project_id}/delivery", response_model=DeliveryDocument)
def page(
    request: Request,
    project_id: int,
    response: Response,
    dataset: str | None = None,
    cursor: str | None = None,
    before_run_id: int | None = None,
    after_run_id: int | None = None,
):
    response.headers.update(HEADERS)
    with session_scope() as session:
        return service.page(
            session,
            project_id,
            delivery_query(request),
            dataset=dataset,
            cursor=cursor,
            before_run_id=before_run_id,
            after_run_id=after_run_id,
        )


@router.get("/api/projects/{project_id}/delivery/export")
def export(
    request: Request,
    project_id: int,
    format: str = "json",
    dataset: str | None = None,
    before_run_id: int | None = None,
    after_run_id: int | None = None,
):
    query = delivery_query(request)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", delete=False
    ) as output:
        path = Path(output.name)
        try:
            with session_scope() as session:
                service.write(
                    session,
                    project_id,
                    query,
                    format,
                    output,
                    dataset=dataset,
                    before_run_id=before_run_id,
                    after_run_id=after_run_id,
                )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    extension = {"openapi": "json", "readable_csv": "csv"}.get(format, format)
    filename = f"garden-{project_id}-{dataset or query.view}.{extension}"
    return FileResponse(
        path,
        media_type=MEDIA[format],
        background=BackgroundTask(path.unlink, missing_ok=True),
        headers={
            **HEADERS,
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Security-Policy": (
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )


@router.get("/projects/{project_id}/delivery", include_in_schema=False)
def delivery_page(
    request: Request,
    project_id: int,
    dataset: str | None = None,
    before_run_id: str | None = None,
    after_run_id: str | None = None,
):
    try:
        before_run_id = int(before_run_id) if before_run_id else None
        after_run_id = int(after_run_id) if after_run_id else None
    except ValueError:
        raise InputValidationError("项目运行 ID 必须为整数。") from None
    query = delivery_query(request).model_copy(update={"page": 1})
    dataset = dataset or query.view
    with session_scope() as session:
        result = service.page(
            session,
            project_id,
            query,
            dataset=dataset,
            before_run_id=before_run_id,
            after_run_id=after_run_id,
        )
    values = query.model_dump(mode="json", exclude_none=True, exclude={"page"})
    values.update(dataset=dataset)
    if before_run_id is not None:
        values.update(before_run_id=before_run_id)
    if after_run_id is not None:
        values.update(after_run_id=after_run_id)
    exports = {
        f: f"/api/projects/{project_id}/delivery/export?" + urlencode(dict(values, format=f))
        for f in ("json", "csv", "markdown", "html")
    }
    if dataset == "observations":
        exports["openapi"] = f"/api/projects/{project_id}/delivery/export?" + urlencode(
            dict(values, format="openapi")
        )
    return templates.TemplateResponse(
        request=request,
        name="projects/delivery.html",
        context={
            "page_title": "资产交付",
            "project_id": project_id,
            "query": query,
            "dataset": dataset,
            "result": result,
            "report_lines": summary_lines(result),
            "exports": exports,
            "before_run_id": before_run_id,
            "after_run_id": after_run_id,
            "api_url": f"/api/projects/{project_id}/delivery?" + urlencode(values),
            "export_request": json.dumps(
                dict(
                    query=query.model_dump(mode="json"),
                    dataset=dataset,
                    before_run_id=before_run_id,
                    after_run_id=after_run_id,
                    expected_revision=result["revision"],
                )
            ),
        },
        headers=HEADERS,
    )


@router.post("/api/projects/{project_id}/exports", status_code=202)
def submit_export(
    request: Request, project_id: int, payload: DeliveryExportRequest, response: Response
):
    response.headers.update(HEADERS)
    with session_scope() as session:
        return request.app.state.export_jobs.submit(session, project_id, payload)


@router.get("/api/exports/{job_id}")
def export_status(request: Request, job_id: str, response: Response):
    response.headers.update(HEADERS)
    with session_scope() as session:
        return request.app.state.export_jobs.view(session, job_id)


@router.post("/api/exports/{job_id}/cancel")
def cancel_export(request: Request, job_id: str, response: Response):
    response.headers.update(HEADERS)
    with session_scope() as session:
        return request.app.state.export_jobs.cancel(session, job_id)


@router.get("/api/exports/{job_id}/download")
def download_export(request: Request, job_id: str):
    with session_scope() as session:
        path, media = request.app.state.export_jobs.artifact(session, job_id)
    return FileResponse(
        path,
        media_type=media,
        filename=f"garden-{job_id}.{path.suffix.lstrip('.')}",
        headers={
            **HEADERS,
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                "font-src data:; base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )


@router.post("/delivery/projects/{project_id}/exports", include_in_schema=False)
def submit_export_form(
    request: Request, project_id: int, configuration: str = Form(...), format: str = Form(...)
):
    try:
        values = json.loads(configuration)
        if not isinstance(values, dict):
            raise InputValidationError("导出配置必须为 JSON 对象。")
        payload = DeliveryExportRequest.model_validate(values | {"format": format})
    except (json.JSONDecodeError, ValidationError) as error:
        raise InputValidationError("导出配置无效，请重新预览。") from error
    with session_scope() as session:
        result = request.app.state.export_jobs.submit(session, project_id, payload)
    return RedirectResponse(f"/exports/{result['id']}", status_code=303, headers=HEADERS)


@router.get("/exports/{job_id}", include_in_schema=False)
def export_job_page(request: Request, job_id: str):
    with session_scope() as session:
        job = request.app.state.export_jobs.view(session, job_id)
    return templates.TemplateResponse(
        request=request,
        name="delivery_job.html",
        context={"job": job, "page_title": "导出任务"},
        headers=HEADERS,
    )


@router.post("/exports/{job_id}/cancel", include_in_schema=False)
def cancel_export_form(request: Request, job_id: str):
    with session_scope() as session:
        request.app.state.export_jobs.cancel(session, job_id)
    return RedirectResponse(f"/exports/{job_id}", status_code=303, headers=HEADERS)
