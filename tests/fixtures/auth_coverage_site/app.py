"""Deterministic localhost site for real-browser authenticated coverage tests."""

from __future__ import annotations

import hashlib
import hmac
import os
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI()

_SIGNING_KEY = b"garden-localhost-e2e-only"
_USERS = {
    "coverage-user": ("GARDEN_E2E_USER_PASSWORD", "user"),
    "coverage-admin": ("GARDEN_E2E_ADMIN_PASSWORD", "admin"),
}


def _sign(role: str) -> str:
    digest = hmac.new(_SIGNING_KEY, role.encode(), hashlib.sha256).hexdigest()
    return f"{role}.{digest}"


def _role(request: Request) -> str:
    raw = request.cookies.get("garden_e2e_role", "")
    role, separator, signature = raw.partition(".")
    if not separator or role not in {"user", "admin"}:
        return "anonymous"
    expected = _sign(role).partition(".")[2]
    return role if hmac.compare_digest(signature, expected) else "anonymous"


def _page(title: str, heading: str, links: list[tuple[str, str]]) -> HTMLResponse:
    anchors = "".join(f'<li><a href="{href}">{label}</a></li>' for href, label in links)
    return HTMLResponse(
        "<!doctype html><html><head>"
        f"<title>{title}</title></head><body><h1>{heading}</h1><ul>{anchors}</ul></body></html>"
    )


@app.get("/", response_class=HTMLResponse)
def coverage_home(request: Request) -> HTMLResponse:
    role = _role(request)
    links = [("/shared", "Shared"), ("/api/profile", "Profile API")]
    if role == "anonymous":
        links.append(("/anonymous-banner", "Anonymous banner"))
    if role in {"user", "admin"}:
        links.append(("/account", "Account"))
    if role == "admin":
        links.append(("/admin/users", "Admin users"))
    return _page(
        "Coverage Home",
        f"{role.title()} coverage",
        links,
    )


@app.get("/login", response_class=HTMLResponse)
def login_form() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head><title>Sign in</title></head><body>"
        '<form method="post" action="/login">'
        '<input name="username" autocomplete="username">'
        '<input name="password" type="password" autocomplete="current-password">'
        '<button type="submit">Sign in</button>'
        "</form></body></html>"
    )


@app.post("/login")
async def login(request: Request):
    fields = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    username = fields.get("username", [""])[0]
    password = fields.get("password", [""])[0]
    configured = _USERS.get(username)
    if configured is None or password != os.environ.get(configured[0]):
        return HTMLResponse("Invalid credentials", status_code=401)
    role = configured[1]
    destination = "/admin/users" if role == "admin" else "/account"
    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(
        "garden_e2e_role",
        _sign(role),
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/shared", response_class=HTMLResponse)
def shared() -> HTMLResponse:
    return _page("Shared", "Same shared content", [])


@app.get("/anonymous-banner", response_class=HTMLResponse)
def anonymous_banner() -> HTMLResponse:
    return _page("Anonymous Banner", "Visible without authentication", [])


@app.get("/account", response_class=HTMLResponse)
def account(request: Request):
    role = _role(request)
    if role not in {"user", "admin"}:
        return RedirectResponse("/login", status_code=303)
    links = [("/shared", "Shared"), ("/account", "Account"), ("/api/profile", "Profile API")]
    return _page("Account", "Authenticated account", links)


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request):
    if _role(request) != "admin":
        return RedirectResponse("/login", status_code=303)
    return _page(
        "Admin Users",
        "Administrative users",
        [
            ("/shared", "Shared"),
            ("/account", "Account"),
            ("/admin/users", "Admin users"),
            ("/api/profile", "Profile API"),
        ],
    )


@app.get("/api/profile")
def profile(request: Request) -> JSONResponse:
    role = _role(request)
    return JSONResponse({"surface": "profile", "role": role})
