"""Local-only human challenge and expiring identity fixture."""

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse


def identity_site():
    app = FastAPI()
    state = {"sessions": {}, "requests": []}

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
        response = RedirectResponse("/me", status_code=303)
        response.set_cookie("identity", token, httponly=True, samesite="lax")
        return response

    @app.get("/{path:path}", response_class=HTMLResponse)
    def page(path: str, request: Request):
        who = state["sessions"].get(request.cookies.get("identity"))
        state["requests"].append((path, who))
        if path in {"me", "asset", "denied"} and not who:
            return RedirectResponse("/login", status_code=302)
        if path == "denied":
            return HTMLResponse("Forbidden", status_code=403)
        if path == "me":
            return f'<div id="identity">{who}</div><a href="/asset">Asset</a>'
        return f'<h1>{who or "anonymous"}</h1><a href="/asset">Asset</a>'

    return app, state
