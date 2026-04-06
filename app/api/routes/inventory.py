"""Server-rendered inventory pages."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.bootstrap import session_scope
from app.services.credentials import CredentialProfileService
from app.services.inventory import InventoryBuildService
from app.services.targets import TargetService

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
router = APIRouter(tags=["inventory"])
service = InventoryBuildService()
target_service = TargetService()
credential_service = CredentialProfileService()


@router.get("/inventory", response_class=HTMLResponse, include_in_schema=False)
def inventory_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        inventory_runs = service.list(session)
        rows = [
            {
                "inventory_run": inventory_run,
                "target_name": target_service.get(session, inventory_run.target_id).name,
                "profile_name": credential_service.get(
                    session,
                    inventory_run.credential_profile_id,
                ).name,
            }
            for inventory_run in inventory_runs
        ]
    return templates.TemplateResponse(
        request=request,
        name="inventory_list.html",
        context={"inventory_runs": rows, "page_title": "Inventory"},
    )


@router.get("/inventory/{inventory_run_id}", response_class=HTMLResponse, include_in_schema=False)
def inventory_detail_page(request: Request, inventory_run_id: int) -> HTMLResponse:
    with session_scope() as session:
        inventory_run = service.get(session, inventory_run_id)
        target_name = target_service.get(session, inventory_run.target_id).name
        profile_name = credential_service.get(session, inventory_run.credential_profile_id).name
    return templates.TemplateResponse(
        request=request,
        name="inventory_detail.html",
        context={
            "inventory_run": inventory_run,
            "target_name": target_name,
            "profile_name": profile_name,
            "page_title": f"Inventory {inventory_run.id}",
        },
    )
