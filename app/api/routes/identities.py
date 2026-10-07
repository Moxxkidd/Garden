"""Identity lifecycle adapters; state payloads never enter public responses."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.core.errors import InputValidationError
from app.db.bootstrap import session_scope
from app.models.auth_session import AuthSession
from app.models.credential_profile import CredentialProfile
from app.models.scan_run import ScanRun
from app.models.target import Target
from app.schemas.identity import (
    IdentityCollectionRequest,
    IdentityPreviewConfirmation,
    IdentityStateImport,
    ManualLoginRequest,
    RecoveryConfirmation,
    RecoveryRequest,
)
from app.schemas.scan import ScanOptions

router = APIRouter(tags=["identities"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def check_origin(request):
    supplied = request.headers.get("origin")
    # no-referrer native forms may send Origin: null; Fetch Metadata still binds the site.
    if supplied == "null" and request.headers.get("sec-fetch-site") == "same-origin":
        return
    if supplied and supplied.rstrip("/") != str(request.base_url).rstrip("/"):
        raise InputValidationError("请从当前 Garden 页面提交。")


async def payload(request, schema):
    check_origin(request)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 2 * 1024 * 1024:
            raise InputValidationError("输入超过大小限制。")
    try:
        return schema.model_validate_json(raw)
    except (ValidationError, ValueError):
        raise InputValidationError("身份请求格式无效，请检查必填字段与输入大小。") from None


def services(request):
    return request.app.state.identity_collection, request.app.state.manual_login


@router.post("/api/identity-runs/preview")
async def preview_run(request: Request):
    data = await payload(request, IdentityCollectionRequest)
    with session_scope() as session:
        return request.app.state.identity_preview.preview(session, data)


@router.post("/api/identity-runs")
async def start_run(request: Request):
    data = await payload(request, IdentityPreviewConfirmation)
    with session_scope() as session:
        run_id = request.app.state.identity_preview.start(session, data.preview_token)
    return {"run_id": run_id, "url": f"/scans/{run_id}"}


@router.post("/api/login-attempts")
async def start_login(request: Request):
    data = await payload(request, ManualLoginRequest)
    with session_scope() as session:
        return services(request)[1].start(session, data)


@router.get("/api/login-attempts/{attempt_id}")
def login_status(request: Request, attempt_id: int):
    with session_scope() as session:
        return services(request)[1].get(session, attempt_id)


@router.post("/api/login-attempts/{attempt_id}/{action}")
def login_action(request: Request, attempt_id: int, action: str):
    check_origin(request)
    if action not in {"confirm", "cancel"}:
        raise InputValidationError("登录操作无效。")
    with session_scope() as session:
        return getattr(services(request)[1], action)(session, attempt_id)


@router.post("/api/identity-sessions/import")
async def import_state(request: Request):
    data = await payload(request, IdentityStateImport)
    return await run_in_threadpool(
        _import_state, request, data.profile_id, data.raw_json, data.verification
    )


def _import_state(request, profile_id, raw_json, verification):
    with session_scope() as session:
        return services(request)[0].sessions.import_state(
            session, profile_id, raw_json, verification
        )


@router.post("/api/identity-sessions/{session_id}/{action}")
def session_action(request: Request, session_id: int, action: str):
    check_origin(request)
    service = services(request)[0].sessions
    with session_scope() as session:
        if action == "validate":
            return service.validate(session, session_id)
        if action == "revoke":
            service.revoke(session, session_id)
            return {"session_id": session_id, "status": "revoked"}
    raise InputValidationError("会话操作无效。")


@router.post("/api/identity-recovery/{action}")
async def recover(request: Request, action: str):
    from app.services.identity_recovery import IdentityRecoveryService

    if action not in {"preview", "start"}:
        raise InputValidationError("补采操作无效。")
    data = await payload(request, RecoveryRequest if action == "preview" else RecoveryConfirmation)
    collection = services(request)[0]
    service = IdentityRecoveryService(storage=collection.storage, collection=collection)
    with session_scope() as session:
        if action == "preview":
            return service.preview(session, data)
        run_id = service.start(
            session,
            RecoveryRequest.model_validate(data.model_dump(exclude={"preview_token"})),
            data.preview_token,
        )
        return {"run_id": run_id, "url": f"/scans/{run_id}"}


@router.get("/identities", response_class=HTMLResponse)
def identity_page(request: Request):
    with session_scope() as session:
        targets = [{"id": t.id, "name": t.name} for t in session.scalars(select(Target))]
        profiles = [
            {"id": p.id, "name": p.name, "role": p.role, "target_id": p.target_id}
            for p in session.scalars(select(CredentialProfile))
        ]
        states = [
            {
                "id": s.id,
                "profile_id": s.credential_profile_id,
                "status": "revoked" if s.revoked_at else s.status,
            }
            for s in session.scalars(
                select(AuthSession).where(AuthSession.identity_metadata.is_not(None))
            )
        ]
        runs = session.scalars(
            select(ScanRun)
            .where(ScanRun.mode == "identity_collection")
            .order_by(ScanRun.id.desc())
            .limit(20)
        ).all()
        run_rows = [
            {
                "id": r.id,
                "status": r.status,
                "parent_run_id": r.parent_run_id,
                "contexts": [
                    {
                        "id": c.id,
                        "key": c.context_key,
                        "checkpoint": (c.identity_snapshot or {}).get("checkpoint_version"),
                    }
                    for c in r.contexts
                ],
            }
            for r in runs
        ]
    return templates.TemplateResponse(
        request=request,
        name="identities.html",
        context={"targets": targets, "profiles": profiles, "states": states, "runs": run_rows},
        headers=_HEADERS,
    )


async def form_data(request):
    check_origin(request)
    # Starlette limits parts; reject large bodies before form decoding as well.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 4 * 1024 * 1024:
            raise InputValidationError("输入超过大小限制。")
    from urllib.parse import parse_qs

    if not request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        raise InputValidationError("表单编码无效。")
    try:
        return parse_qs(raw.decode(), keep_blank_values=True, max_num_fields=50)
    except (ValueError, UnicodeError):
        raise InputValidationError("表单格式无效。") from None


def one(form, name, default=""):
    return form.get(name, [default])[0]


@router.post("/identities/preview", response_class=HTMLResponse)
async def form_preview(request: Request):
    form = await form_data(request)
    try:
        data = IdentityCollectionRequest(
            url=one(form, "url"),
            target_id=one(form, "target_id"),
            profile_ids=form.get("profile_ids", []),
            include_anonymous="anonymous" in form,
            options=ScanOptions(max_pages=one(form, "max_pages", "50")),
        )
    except (ValidationError, ValueError):
        raise InputValidationError("请填写目标、入口，并选择身份或匿名。") from None
    with session_scope() as session:
        preview = request.app.state.identity_preview.preview(session, data)
    return templates.TemplateResponse(
        request=request,
        name="identity_preview.html",
        context={"preview": preview},
        headers=_HEADERS,
    )


@router.post("/identities/start")
async def form_start(request: Request):
    form = await form_data(request)
    with session_scope() as session:
        run_id = request.app.state.identity_preview.start(session, one(form, "preview_token"))
    return RedirectResponse(f"/scans/{run_id}", status_code=303, headers=_HEADERS)


@router.post("/identities/auth/{action}")
async def form_auth(request: Request, action: str):
    form = await form_data(request)
    try:
        config = ManualLoginRequest(
            profile_id=one(form, "profile_id"),
            login_url=one(form, "login_url"),
            validate_url=one(form, "validate_url"),
            success_selector=one(form, "success_selector") or None,
            success_text=one(form, "success_text") or None,
            allowed_auth_origins=one(form, "allowed_auth_origins").split(),
        )
    except (ValidationError, ValueError):
        raise InputValidationError("请填写身份、登录地址、验证地址及正向验证条件。") from None
    with session_scope() as session:
        if action == "login":
            attempt = services(request)[1].start(session, config)
            return RedirectResponse(
                f"/identities/login/{attempt.id}", status_code=303, headers=_HEADERS
            )
        if action == "import":
            health = await run_in_threadpool(
                _import_state, request, config.profile_id, one(form, "raw_json"), config
            )
            if health.status != "ready":
                raise InputValidationError("恢复验证未通过，状态未保存。请重新认证并核对成功条件。")
        else:
            raise InputValidationError("操作无效。")
    return RedirectResponse("/identities", status_code=303, headers=_HEADERS)


@router.get("/identities/login/{attempt_id}", response_class=HTMLResponse)
def form_login_status(request: Request, attempt_id: int):
    with session_scope() as session:
        attempt = services(request)[1].get(session, attempt_id)
    return templates.TemplateResponse(
        request=request, name="identity_login.html", context={"attempt": attempt}, headers=_HEADERS
    )


@router.post("/identities/login/{attempt_id}/{action}")
def form_login_action(request: Request, attempt_id: int, action: str):
    login_action(request, attempt_id, action)
    return RedirectResponse(f"/identities/login/{attempt_id}", status_code=303, headers=_HEADERS)


@router.post("/identities/sessions/{session_id}/{action}")
def form_session_action(request: Request, session_id: int, action: str):
    session_action(request, session_id, action)
    return RedirectResponse("/identities", status_code=303, headers=_HEADERS)


@router.post("/api/identity-profiles/{profile_id}/activate")
def activate_profile(request: Request, profile_id: int):
    check_origin(request)
    with session_scope() as session:
        return services(request)[0].sessions.activate_automatic_profile(session, profile_id)


@router.post("/identities/profiles/{profile_id}/activate")
def form_activate_profile(request: Request, profile_id: int):
    activate_profile(request, profile_id)
    return RedirectResponse("/identities", status_code=303, headers=_HEADERS)


@router.post("/identities/profiles")
async def create_profile(request: Request):
    from sqlalchemy.exc import IntegrityError

    form = await form_data(request)
    try:
        target_id = int(one(form, "target_id"))
        name, role = one(form, "name").strip(), one(form, "role").strip()
        if not name or not role or max(len(name), len(role)) > 120:
            raise ValueError
    except ValueError:
        raise InputValidationError("请填写有效目标、身份名称和角色标签。") from None
    try:
        with session_scope() as session:
            if not session.get(Target, target_id):
                raise InputValidationError("目标不存在。")
            session.add(
                CredentialProfile(
                    target_id=target_id,
                    name=name,
                    role=role,
                    username=name,
                    auth_type="cookie",
                    secret_ref="manual",
                    login_config_path="manual",
                )
            )
    except IntegrityError:
        raise InputValidationError("该目标下已有同名身份，请更换名称。") from None
    return RedirectResponse("/identities", status_code=303, headers=_HEADERS)


@router.post("/identities/recovery/{action}")
async def form_recovery(request: Request, action: str):
    from app.services.identity_recovery import IdentityRecoveryService

    form = await form_data(request)
    try:
        data = RecoveryRequest(
            source_run_id=one(form, "source_run_id"),
            source_context_id=one(form, "source_context_id"),
            checkpoint_version=one(form, "checkpoint_version"),
        )
    except ValidationError:
        raise InputValidationError("补采来源无效。") from None
    collection = services(request)[0]
    service = IdentityRecoveryService(storage=collection.storage, collection=collection)
    with session_scope() as session:
        if action == "preview":
            preview = service.preview(session, data)
            return templates.TemplateResponse(
                request=request,
                name="identity_recovery.html",
                context={"preview": preview},
                headers=_HEADERS,
            )
        if action == "start":
            run_id = service.start(session, data, one(form, "preview_token"))
            return RedirectResponse(f"/scans/{run_id}", status_code=303, headers=_HEADERS)
    raise InputValidationError("补采操作无效。")
