"""Local-only human challenge and expiring identity fixture."""

import asyncio
import threading
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse


def identity_site():
    app = FastAPI()
    state = {"sessions": {}, "requests": [], "posts": [], "post_started": threading.Event()}

    @app.get("/login", response_class=HTMLResponse)
    def login():
        return (
            '<form method="post"><input name="username">'
            '<input name="code"><button>Login</button></form>'
        )

    @app.post("/login")
    def authenticate(username: str = Form(), code: str = Form()):
        if code != "123456":
            return HTMLResponse("Challenge required", status_code=401)
        token = "private-" + username
        state["sessions"][token] = username
        destination = (
            state["return_to"] + "?" + urlencode({"username": username})
            if state.get("return_to")
            else "/me"
        )
        response = RedirectResponse(destination, status_code=303)
        response.set_cookie("identity", token, httponly=True, samesite="lax")
        return response

    @app.post("/submit")
    async def submit(request: Request):
        who = state["sessions"].get(request.cookies.get("identity"))
        state["post_started"].set()
        await asyncio.sleep(0.2)
        state["posts"].append(who)
        return HTMLResponse("<p>Saved user action</p>", status_code=200 if who else 401)

    @app.post("/expire")
    def expire(request: Request):
        state["sessions"].pop(request.cookies.get("identity"), None)
        return HTMLResponse("Expired", status_code=401)

    @app.get("/{path:path}", response_class=HTMLResponse)
    def page(path: str, request: Request):
        who = state["sessions"].get(request.cookies.get("identity"))
        state["requests"].append((path, who))
        if path == "sso-return":
            username = request.query_params["username"]
            token = "private-" + username
            state["sessions"][token] = username
            response = RedirectResponse("/me", status_code=303)
            response.set_cookie("identity", token, httponly=True, samesite="lax")
            return response
        if path in {"me", "asset", "denied"} and not who:
            return RedirectResponse("/login", status_code=302)
        if path == "restore-hop":
            return RedirectResponse("/me", status_code=302)
        if path == "denied":
            return HTMLResponse("Forbidden", status_code=403)
        if path == "me":
            controls = (
                ""
                if not state.get("manual")
                else (
                    "<button onclick=\"fetch('/submit', {method: 'POST', "
                    "body: 'token=SYNTHETIC_PRIVATE'})\">User POST</button>"
                    "<button onclick=\"fetch('/expire', {method: 'POST'})\">"
                    "Expire identity</button>"
                    "<button onclick=\"for(let i=0;i<30;i++) fetch('/asset?i='+i)\">"
                    "Use budget</button>"
                    '<a href="'
                    + state.get("cross_origin", "http://localhost:1")
                    + '/foreign-only">Other origin</a>'
                )
            )
            return (
                f'<link rel="icon" href="data:,"><div id="identity">{who}</div>'
                f'<a href="/asset">Asset</a>{controls}'
            )
        return (
            f'<link rel="icon" href="data:,"><h1>{who or "anonymous"}</h1>'
            '<a href="/asset">Asset</a>'
        )

    return app, state
