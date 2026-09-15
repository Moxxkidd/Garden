"""Read-only anonymous preview and explicit server-validated confirmation."""

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.db.bootstrap import session_scope
from app.schemas.assessment import AssessmentStartRequest
from app.schemas.scan import ScanOptions
from app.services.scan_submission_preview import build_preview

router = APIRouter(tags=["scans"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
# Process-local, fixed-size key: restart or another worker requires a fresh preview.
_SIGNING_KEY = secrets.token_bytes(32)
_PREVIEW_TTL_SECONDS = 600


def _signature(timestamp, values, preview, settings):
    binding = json.dumps(
        [timestamp, values, preview.model_dump(mode="json"), settings.model_dump(mode="json")],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hmac.new(_SIGNING_KEY, binding, hashlib.sha256).hexdigest()


def _valid_token(token, values, preview, settings):
    try:
        timestamp, signature = token.split(".", 1)
        age = time.time() - int(timestamp)
        return 0 <= age <= _PREVIEW_TTL_SECONDS and hmac.compare_digest(
            signature, _signature(timestamp, values, preview, settings)
        )
    except (ValueError, TypeError):
        return False


def _render(request, values, *, preview=None, errors=None, status_code=200):
    errors = errors or {}
    token = None
    if preview and preview.can_submit:
        timestamp = str(int(time.time()))
        token = (
            timestamp
            + "."
            + _signature(timestamp, values, preview, request.app.state.scan_service.settings)
        )
    return templates.TemplateResponse(
        request=request,
        name="scan_preview.html",
        context={
            "preview": preview,
            "values": values,
            "errors": errors,
            "preview_token": token,
            "page_title": "预览扫描",
        },
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


async def _submit(request, *, confirm=False):
    form = await request.form()
    values = {key: str(form.get(key, "")) for key in ("url", *ScanOptions.model_fields)}
    try:
        payload = AssessmentStartRequest(
            url=values["url"],
            options={k: v for k, v in values.items() if k != "url" and v != ""},
        )
    except ValidationError as exc:
        errors = {
            str(error["loc"][-1]): "请填写有效值，并遵守该字段的范围。"
            for error in exc.errors(include_input=False)
        }
        return _render(request, values, errors=errors, status_code=422)
    settings = request.app.state.scan_service.settings
    with session_scope() as session:
        preview = build_preview(session, payload, settings)
    if not preview.can_submit:
        errors = {
            issue.field: issue.message for issue in preview.issues if issue.severity == "error"
        }
        return _render(request, values, preview=preview, errors=errors, status_code=422)
    if confirm:
        if not _valid_token(str(form.get("preview_token", "")), values, preview, settings):
            return _render(
                request,
                values,
                errors={"preview_token": "预览已过期或参数、配置发生变化，请重新预览。"},
                status_code=409,
            )
        scan = request.app.state.scan_service.start_scan(payload.url, preview.effective_options)
        return RedirectResponse(url=f"/scans/{scan.id}", status_code=303)
    return _render(request, values, preview=preview)


@router.post("/scans/preview", include_in_schema=False)
async def preview_scan(request: Request):
    return await _submit(request)


@router.post("/scans/confirm", include_in_schema=False)
async def confirm_scan(request: Request):
    return await _submit(request, confirm=True)


@router.post("/scans/edit", include_in_schema=False)
async def edit_scan(request: Request):
    form = await request.form()
    values = {key: str(form.get(key, "")) for key in ("url", *ScanOptions.model_fields)}
    return _render(request, values)
