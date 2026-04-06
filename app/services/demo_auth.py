"""Local demo authentication backend for HTTP and UI login flows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe

from app.core.settings import get_settings

DEMO_COOKIE_NAME = "garden_demo_auth"


@dataclass
class DemoUser:
    username: str
    password: str
    role: str


_DEMO_SESSION_STORE: dict[str, dict[str, object]] = {}


def get_demo_users() -> dict[str, DemoUser]:
    settings = get_settings()
    admin_password = getattr(settings, "demo_admin_password", "demo-admin-password")
    user_password = getattr(settings, "demo_user_password", "demo-user-password")
    return {
        "admin@example.local": DemoUser(
            username="admin@example.local",
            password=admin_password,
            role="admin",
        ),
        "user@example.local": DemoUser(
            username="user@example.local",
            password=user_password,
            role="user",
        ),
    }


def authenticate_demo_user(username: str, password: str) -> DemoUser | None:
    users = get_demo_users()
    user = users.get(username)
    if user is None or user.password != password:
        return None
    return user


def create_demo_session(user: DemoUser, ttl_seconds: int = 600) -> dict[str, object]:
    token = token_urlsafe(18)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    record = {
        "token": token,
        "username": user.username,
        "role": user.role,
        "expires_at": expires_at,
    }
    _DEMO_SESSION_STORE[token] = record
    return record


def get_demo_session(token: str | None) -> dict[str, object] | None:
    if token is None:
        return None
    record = _DEMO_SESSION_STORE.get(token)
    if record is None:
        return None
    expires_at = record["expires_at"]
    if isinstance(expires_at, datetime) and expires_at < datetime.now(timezone.utc):
        return None
    return record


def refresh_demo_session(token: str | None, ttl_seconds: int = 600) -> dict[str, object] | None:
    record = get_demo_session(token)
    if record is None:
        return None
    record["expires_at"] = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    return record
