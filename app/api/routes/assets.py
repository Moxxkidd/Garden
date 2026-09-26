"""Unified, read-only asset list and downloads for both persisted source types."""

from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select

from app.core.errors import InputValidationError
from app.db.bootstrap import session_scope
from app.models.inventory_run import InventoryRun
from app.models.scan_run import ScanRun
from app.schemas.assets import AssetPage, AssetQuery
from app.services.asset_catalog import KINDS, OBSERVATIONS, AssetCatalogService, safe_url
from app.services.asset_export import export_assets

router = APIRouter(tags=["assets"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
service = AssetCatalogService()
_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def parse_asset_query(**values) -> AssetQuery:
    for name in ("kind", "context", "observation"):
        if values.get(name) == "":
            values[name] = None
    try:
        return AssetQuery(**values)
    except ValidationError as error:
        fields = ", ".join(dict.fromkeys(str(e["loc"][0]) for e in error.errors()))
        raise InputValidationError(f"资产查询参数无效：{fields}。") from None


def asset_query(
    source: str,
    run_id: int = Query(gt=0),
    kind: str | None = None,
    context: str | None = None,
    observation: str | None = None,
    q: str = Query(default="", max_length=200),
    sort: str = "id",
    order: str = "asc",
    page: int = Query(default=1, ge=1, le=1000000),
    page_size: int = Query(default=50, ge=1, le=200),
) -> AssetQuery:
    return parse_asset_query(
        source=source,
        run_id=run_id,
        kind=kind,
        context=context,
        observation=observation,
        q=q,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )


@router.get("/api/assets", response_model=AssetPage)
def list_assets(response: Response, query: Annotated[AssetQuery, Depends(asset_query)]):
    response.headers.update(_HEADERS)
    with session_scope() as session:
        return service.list(session, query)


@router.get("/api/assets/export")
def download_assets(
    query: Annotated[AssetQuery, Depends(asset_query)], format: Literal["json", "csv"] = "json"
):
    with session_scope() as session:
        content = export_assets(session, query, format)
    return Response(
        content,
        media_type="application/json" if format == "json" else "text/csv",
        headers={
            **_HEADERS,
            "Content-Disposition": (
                f'attachment; filename="garden-assets-{query.source}-{query.run_id}.{format}"'
            ),
        },
    )


def _link(query: AssetQuery, path: str = "/assets", **changes) -> str:
    values = query.model_dump(exclude_none=True)
    values.update(changes)
    return path + "?" + urlencode(values)


@router.get("/assets", response_class=HTMLResponse, include_in_schema=False)
def assets_page(request: Request):
    values = dict(request.query_params)
    with session_scope() as session:
        if not values.get("source") and not values.get("run_id"):
            choices = []
            for source, model, url_field in [
                ("scan", ScanRun, "normalized_url"),
                ("inventory", InventoryRun, "started_from_url"),
            ]:
                for run in session.scalars(select(model).order_by(model.id.desc()).limit(20)):
                    choices.append(
                        {
                            "source": source,
                            "run_id": run.id,
                            "url": safe_url(getattr(run, url_field)),
                            "status": run.status,
                        }
                    )
            return templates.TemplateResponse(
                request=request,
                name="assets.html",
                context={"page_title": "统一资产清单", "result": None, "choices": choices},
                headers=_HEADERS,
            )
        query = parse_asset_query(**values)
        result = service.list(session, query)
    pages = max(1, (result.matched + query.page_size - 1) // query.page_size)
    contexts = sorted(
        {c.kind for c in result.scope.contexts}
        | {"anonymous", "user", "admin", "unknown"}
        | ({query.context} if query.context else set())
    )
    return templates.TemplateResponse(
        request=request,
        name="assets.html",
        context={
            "page_title": "统一资产清单",
            "result": result,
            "query": query,
            "kinds": KINDS,
            "observations": OBSERVATIONS,
            "contexts": contexts,
            "pages": pages,
            "previous_url": _link(query, page=min(query.page - 1, pages))
            if query.page > 1
            else None,
            "next_url": _link(query, page=query.page + 1) if query.page < pages else None,
            "json_url": _link(query, "/api/assets/export", format="json", page=1),
            "csv_url": _link(query, "/api/assets/export", format="csv", page=1),
        },
        headers=_HEADERS,
    )
