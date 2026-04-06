"""Local demo auth routes for safe Phase 3 verification."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.services.demo_auth import (
    DEMO_COOKIE_NAME,
    authenticate_demo_user,
    create_demo_session,
    get_demo_session,
    refresh_demo_session,
)

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
router = APIRouter(prefix="/demo/auth", tags=["demo-auth"])


def _current_demo_home_url(request: Request) -> str:
    return str(request.url_for("ui_home"))


def _require_demo_session(request: Request) -> dict[str, object] | None:
    return get_demo_session(request.cookies.get(DEMO_COOKIE_NAME))


@router.post("/http/login")
async def http_login(request: Request) -> JSONResponse:
    payload = await request.json()
    user = authenticate_demo_user(
        username=str(payload.get("username", "")).strip(),
        password=str(payload.get("password", "")).strip(),
    )
    if user is None:
        return JSONResponse(
            status_code=401, content={"authenticated": False, "detail": "login rejected"}
        )
    session_record = create_demo_session(user)
    response = JSONResponse(
        content={
            "authenticated": True,
            "role": user.role,
            "expires_at": session_record["expires_at"].isoformat(),
            "current_url": _current_demo_home_url(request),
        }
    )
    response.set_cookie(
        DEMO_COOKIE_NAME,
        session_record["token"],
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/http/me")
def http_me(request: Request) -> JSONResponse:
    session_record = get_demo_session(request.cookies.get(DEMO_COOKIE_NAME))
    if session_record is None:
        return JSONResponse(
            status_code=401, content={"authenticated": False, "detail": "session expired"}
        )
    return JSONResponse(
        content={
            "authenticated": True,
            "username": session_record["username"],
            "role": session_record["role"],
            "expires_at": session_record["expires_at"].isoformat(),
            "current_url": _current_demo_home_url(request),
        }
    )


@router.post("/http/refresh")
def http_refresh(request: Request) -> JSONResponse:
    session_record = refresh_demo_session(request.cookies.get(DEMO_COOKIE_NAME))
    if session_record is None:
        return JSONResponse(
            status_code=401, content={"refreshed": False, "detail": "session expired"}
        )
    response = JSONResponse(
        content={
            "refreshed": True,
            "role": session_record["role"],
            "expires_at": session_record["expires_at"].isoformat(),
            "current_url": _current_demo_home_url(request),
        }
    )
    response.set_cookie(DEMO_COOKIE_NAME, session_record["token"], httponly=True, samesite="lax")
    return response


@router.get("/ui/login", response_class=HTMLResponse)
def ui_login_page(request: Request, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="demo_login.html",
        context={"page_title": "Demo Login", "error": error},
    )


@router.post("/ui/login")
def ui_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> Response:
    user = authenticate_demo_user(username=username.strip(), password=password.strip())
    if user is None:
        return templates.TemplateResponse(
            request=request,
            name="demo_login.html",
            context={"page_title": "Demo Login", "error": "Login rejected"},
            status_code=401,
        )
    session_record = create_demo_session(user)
    response = RedirectResponse(url="/demo/auth/ui/home", status_code=303)
    response.set_cookie(DEMO_COOKIE_NAME, session_record["token"], httponly=True, samesite="lax")
    return response


@router.get("/ui/home", response_class=HTMLResponse)
def ui_home(request: Request) -> HTMLResponse:
    session_record = _require_demo_session(request)
    if session_record is None:
        return RedirectResponse(url="/demo/auth/ui/login?error=session-expired", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="demo_home.html",
        context={"page_title": "Demo Home", "session_record": session_record},
    )


@router.get("/app/admin/users", response_class=HTMLResponse)
def demo_admin_users(request: Request) -> Response:
    session_record = _require_demo_session(request)
    if session_record is None:
        return RedirectResponse(url="/demo/auth/ui/login?error=session-expired", status_code=303)
    response = templates.TemplateResponse(
        request=request,
        name="demo_inventory_page.html",
        context={
            "page_title": "Admin Users",
            "section_label": "Admin",
            "heading": "Manage Users",
            "description": (
                "Inventory demo page for admin and user-management coverage. "
                "Debug toolbar is enabled for staging-admin.internal.local."
            ),
            "session_record": session_record,
            "page_key": "admin_users",
        },
    )
    response.headers["cache-control"] = "public, max-age=600"
    return response


@router.get("/app/manage/config", response_class=HTMLResponse)
def demo_manage_config(request: Request) -> Response:
    session_record = _require_demo_session(request)
    if session_record is None:
        return RedirectResponse(url="/demo/auth/ui/login?error=session-expired", status_code=303)
    response = templates.TemplateResponse(
        request=request,
        name="demo_inventory_page.html",
        context={
            "page_title": "Manage Config",
            "section_label": "Config",
            "heading": "Config Console",
            "description": (
                "Inventory demo page for config, import, and actuator coverage. "
                "Review /srv/garden/config/settings.py on config-node.internal.local for staging."
            ),
            "session_record": session_record,
            "page_key": "manage_config",
        },
    )
    response.headers["cache-control"] = "public, max-age=300"
    return response


@router.get("/app/export-center", response_class=HTMLResponse)
def demo_export_center(request: Request) -> Response:
    session_record = _require_demo_session(request)
    if session_record is None:
        return RedirectResponse(url="/demo/auth/ui/login?error=session-expired", status_code=303)
    response = templates.TemplateResponse(
        request=request,
        name="demo_inventory_page.html",
        context={
            "page_title": "Export Center",
            "section_label": "Export",
            "heading": "Export and Upload Hub",
            "description": (
                "Inventory demo page for export, download, and upload coverage. "
                "Build 2026.04.1 trace_id=demo-trace-001 support@app.internal.local."
            ),
            "session_record": session_record,
            "page_key": "export_center",
        },
    )
    response.headers["cache-control"] = "public, max-age=900"
    return response


@router.get("/app/api/bootstrap")
def demo_api_bootstrap(request: Request, session: str = "", role: str = "") -> JSONResponse:
    session_record = _require_demo_session(request)
    if session_record is None:
        return JSONResponse(status_code=401, content={"detail": "session expired"})
    return JSONResponse({"ok": True, "session": session, "role": role})


@router.get("/app/api/admin/users")
def demo_api_admin_users(request: Request, userId: str = "", role: str = "") -> JSONResponse:
    session_record = _require_demo_session(request)
    if session_record is None:
        return JSONResponse(status_code=401, content={"detail": "session expired"})
    return JSONResponse(
        {
            "ok": True,
            "userId": userId,
            "role": role,
            "viewer": session_record["username"],
            "traceId": "trace-admin-001",
            "support": "support@app.internal.local",
        },
        headers={"cache-control": "private, no-store"},
    )


@router.post("/app/api/admin/export")
async def demo_api_admin_export(request: Request) -> JSONResponse:
    session_record = _require_demo_session(request)
    if session_record is None:
        return JSONResponse(status_code=401, content={"detail": "session expired"})
    payload = await request.json()
    return JSONResponse(
        {
            "ok": True,
            "accepted": payload,
            "viewer": session_record["username"],
            "version": "2026.04.1",
        },
        headers={"cache-control": "private, no-store"},
    )


@router.get("/app/api/manage/config")
def demo_api_manage_config(
    request: Request,
    debug: str = "",
    actuator: str = "",
) -> JSONResponse:
    session_record = _require_demo_session(request)
    if session_record is None:
        return JSONResponse(status_code=401, content={"detail": "session expired"})
    return JSONResponse(
        {
            "ok": True,
            "debug": debug,
            "actuator": actuator,
            "viewer": session_record["username"],
            "banner": "DEBUG toolbar enabled",
            "traceback": "Traceback (most recent call last): File '/srv/garden/app.py', line 42",
            "internalHost": "config-node.internal.local",
        },
        headers={
            "cache-control": "public, max-age=600",
            "set-cookie": "debug_pref=enabled; Path=/",
        },
    )


@router.post("/app/api/manage/import")
def demo_api_manage_import(
    request: Request,
    key: str = Form(""),
    secret: str = Form(""),
) -> JSONResponse:
    session_record = _require_demo_session(request)
    if session_record is None:
        return JSONResponse(status_code=401, content={"detail": "session expired"})
    return JSONResponse(
        {
            "ok": True,
            "key": key,
            "secret": bool(secret),
            "viewer": session_record["username"],
            "environment": "staging",
        },
        headers={"cache-control": "private, no-store"},
    )


@router.get("/app/api/export/download")
def demo_api_export_download(
    request: Request,
    format: str = "",
    session: str = "",
) -> JSONResponse:
    session_record = _require_demo_session(request)
    if session_record is None:
        return JSONResponse(status_code=401, content={"detail": "session expired"})
    return JSONResponse(
        {
            "ok": True,
            "format": format,
            "session": session,
            "viewer": session_record["username"],
            "traceId": "trace-export-101",
        },
        headers={"cache-control": "public, max-age=600"},
    )


@router.post("/app/api/upload")
def demo_api_upload(
    request: Request,
    password: str = Form(""),
    token: str = Form(""),
) -> JSONResponse:
    session_record = _require_demo_session(request)
    if session_record is None:
        return JSONResponse(status_code=401, content={"detail": "session expired"})
    return JSONResponse(
        {
            "ok": True,
            "password": bool(password),
            "token": bool(token),
            "viewer": session_record["username"],
            "environment": "sandbox",
        },
        headers={"cache-control": "private, no-store"},
    )


@router.get("/app/api/actuator/health")
def demo_api_actuator_health(request: Request, key: str = "") -> JSONResponse:
    session_record = _require_demo_session(request)
    if session_record is None:
        return JSONResponse(status_code=401, content={"detail": "session expired"})
    return JSONResponse(
        {
            "status": "UP",
            "key": key,
            "viewer": session_record["username"],
            "version": "1.4.2",
            "internalHost": "health-node.internal.local",
        },
        headers={"cache-control": "private, no-store"},
    )
