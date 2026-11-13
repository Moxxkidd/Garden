"""Instance-scoped local control, enabled only by the managed runtime launcher."""

import os
import secrets
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.core.errors import InputValidationError
from app.core.version import project_version


def configure_local_session(app):
    token = os.environ.get("GARDEN_RUNTIME_TOKEN")
    if not token:
        return
    from app.api.routes.identities import check_origin
    from app.api.routes.local_runtime import router

    app.include_router(router)
    instance = os.environ["GARDEN_RUNTIME_INSTANCE"]
    origin = "http://127.0.0.1:" + os.environ["GARDEN_RUNTIME_PORT"]
    home = str(Path(os.environ["GARDEN_HOME"]).resolve())

    @app.middleware("http")
    async def local_session(request: Request, call_next):
        if str(request.base_url).rstrip("/") != origin:
            return JSONResponse({"detail": "local_host_mismatch"}, status_code=403)
        try:
            check_origin(request)
        except InputValidationError:
            return JSONResponse({"detail": "local_origin_mismatch"}, status_code=403)
        if request.url.path == "/_garden/example" and request.method in {"GET", "HEAD"}:
            return HTMLResponse(
                "<h1>Garden 本地合成示例</h1><p>仅包含演示资产，无真实身份或目标数据。</p>"
                '<a href="/_garden/example?view=catalog">Catalog</a>'
                '<script>document.body.dataset.rendered="true";</script>'
            )
        if request.url.path == "/healthz":
            return await call_next(request)
        if request.url.path == "/_garden/session" and request.method == "GET":
            nonce = secrets.token_urlsafe(18)
            # A fragment never reaches HTTP logs or Referer. Remove it before loading WebUI.
            return HTMLResponse(
                '<!doctype html><meta charset="utf-8"><title>连接 Garden</title>'
                '<p id="status">正在连接本地 Garden…</p>'
                f'<script nonce="{nonce}">'
                'const t=location.hash.slice(1);history.replaceState(null,"","/_garden/session");'
                'fetch("/_garden/session/exchange",{method:"POST",'
                'headers:{Authorization:"Bearer "+t}})'
                '.then(r=>{if(!r.ok)throw Error("会话已失效，请重新打开");'
                'location.replace("/")})'
                '.catch(e=>document.getElementById("status").textContent=e.message);</script>',
                headers={
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer",
                    "Content-Security-Policy": (
                        f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'"
                    ),
                },
            )
        supplied = request.headers.get("authorization", "")
        authenticated = secrets.compare_digest(
            supplied, "Bearer " + token
        ) or secrets.compare_digest(request.cookies.get("garden_session", ""), token)
        if not authenticated:
            return JSONResponse({"detail": "local_session_required"}, status_code=401)
        if request.url.path == "/_garden/session/exchange" and request.method == "POST":
            response = Response(status_code=204)
            response.set_cookie("garden_session", token, httponly=True, samesite="strict")
            response.headers["Cache-Control"] = "no-store"
            return response
        if request.url.path == "/_garden/runtime" and request.method == "GET":
            return JSONResponse(
                dict(
                    application="garden",
                    version=project_version(),
                    home=home,
                    instance_id=instance,
                    pid=os.getpid(),
                ),
                headers={"Cache-Control": "no-store"},
            )
        if (
            getattr(app.state, "local_stopping", False)
            and request.method not in {"GET", "HEAD"}
            and not request.url.path.startswith("/_garden/")
        ):
            return JSONResponse({"detail": "local_runtime_stopping"}, status_code=409)
        return await call_next(request)
