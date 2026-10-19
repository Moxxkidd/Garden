"""Thin API and native Web adapters for versioned project delivery."""

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates

from app.api.routes.projects import parse_query
from app.core.errors import InputValidationError
from app.db.bootstrap import session_scope
from app.schemas.delivery import DeliveryDocument
from app.services.asset_delivery import AssetDeliveryService

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
    with session_scope() as session:
        content = service.export(
            session,
            project_id,
            delivery_query(request),
            format,
            dataset=dataset,
            before_run_id=before_run_id,
            after_run_id=after_run_id,
        )
    extension = "json" if format == "openapi" else format
    filename = f"garden-{project_id}-{dataset or delivery_query(request).view}.{extension}"
    return Response(
        content,
        media_type=MEDIA[format],
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
            "exports": exports,
            "before_run_id": before_run_id,
            "after_run_id": after_run_id,
            "api_url": f"/api/projects/{project_id}/delivery?" + urlencode(values),
        },
        headers=HEADERS,
    )
