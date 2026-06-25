"""Auth configuration and normalized result schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.errors import InputValidationError
from app.models.enums import LoginFailureReason, SessionStatus, SessionType


class HttpRequestConfig(BaseModel):
    method: Literal["GET", "POST"] = "POST"
    url: str = Field(min_length=1)
    body_type: Literal["json", "form"] = "json"
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    expected_status: int = 200
    success_contains: str | None = None

    @field_validator("url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise InputValidationError("Request URLs cannot be blank.")
        return cleaned


class HttpLoginConfig(BaseModel):
    adapter: Literal["http"] = "http"
    request_timeout_seconds: int = Field(default=5, ge=1, le=30)
    retry_attempts: int = Field(default=1, ge=1, le=3)
    login_request: HttpRequestConfig
    validate_request: HttpRequestConfig
    refresh_request: HttpRequestConfig | None = None
    session_type: SessionType = SessionType.HTTP_COOKIE_JAR


class PlaywrightLoginConfig(BaseModel):
    adapter: Literal["playwright"] = "playwright"
    request_timeout_seconds: int = Field(default=10, ge=1, le=60)
    retry_attempts: int = Field(default=1, ge=1, le=2)
    login_url: str = Field(min_length=1)
    validate_url: str = Field(min_length=1)
    username_selector: str = Field(default="auto", min_length=1)
    password_selector: str = Field(default="auto", min_length=1)
    submit_selector: str = Field(default="auto", min_length=1)
    success_selector: str | None = None
    success_text: str | None = None
    success_url_contains: str | None = None
    error_selector: str | None = None
    refresh_via_relogin: bool = False
    auto_detect_selectors: bool = False

    @field_validator(
        "login_url", "validate_url", "username_selector", "password_selector", "submit_selector"
    )
    @classmethod
    def strip_required_fields(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise InputValidationError("Playwright config fields cannot be blank.")
        return cleaned


LoginConfig = HttpLoginConfig | PlaywrightLoginConfig


class LoginExecutionResult(BaseModel):
    success: bool
    adapter: str
    target_name: str
    profile_name: str
    status: SessionStatus
    session_type: SessionType | None = None
    failure_reason: LoginFailureReason | None = None
    message: str
    expires_at: datetime | None = None
    refresh_supported: bool = False
    session_metadata_redacted: dict[str, Any] = Field(default_factory=dict)
    storage_payload: dict[str, Any] = Field(default_factory=dict)
    last_error: str | None = None
    created_session_id: int | None = None


class SessionValidationResult(BaseModel):
    valid: bool
    status: SessionStatus
    failure_reason: LoginFailureReason | None = None
    message: str
    expires_at: datetime | None = None
    session_metadata_redacted: dict[str, Any] = Field(default_factory=dict)
    last_error: str | None = None
