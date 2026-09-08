"""Configurable HTTP login adapter."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import httpx

from app.core.errors import TargetPolicyError
from app.core.settings import get_settings
from app.models.credential_profile import CredentialProfile
from app.models.enums import LoginFailureReason, SessionStatus
from app.models.target import Target
from app.redaction.display import redact_session_metadata
from app.schemas.auth import (
    HttpLoginConfig,
    HttpRequestConfig,
    LoginExecutionResult,
    SessionValidationResult,
)
from app.services.authenticated_network import AuthenticatedNetworkGuard
from app.services.scan_network import TargetNetworkPolicy

_TEMPLATE_PATTERN = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")


class HttpLoginAdapter:
    adapter_name = "http"

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        policy: TargetNetworkPolicy | None = None,
    ) -> None:
        self.transport = transport
        self.network_guard = AuthenticatedNetworkGuard(
            policy or TargetNetworkPolicy(get_settings())
        )

    def login(
        self,
        target: Target,
        credential_profile: CredentialProfile,
        secret_value: str,
        config: HttpLoginConfig,
        *,
        before_request: Callable[[], None] | None = None,
    ) -> LoginExecutionResult:
        context = self._build_template_context(target, credential_profile, secret_value)
        last_exception: Exception | None = None
        for _ in range(config.retry_attempts):
            try:
                with self._build_client(
                    timeout=config.request_timeout_seconds,
                    target=target,
                    before_request=before_request,
                ) as client:
                    response = self._send_request(client, target, config.login_request, context)
                    if response.status_code in {401, 403}:
                        return self._failure(
                            target,
                            credential_profile,
                            LoginFailureReason.LOGIN_REJECTED,
                            "HTTP login was rejected by the target.",
                        )
                    if response.status_code != config.login_request.expected_status:
                        return self._failure(
                            target,
                            credential_profile,
                            LoginFailureReason.UNEXPECTED_RESPONSE,
                            "Expected HTTP "
                            f"{config.login_request.expected_status} but "
                            f"received {response.status_code}.",
                        )
                    if (
                        config.login_request.success_contains
                        and config.login_request.success_contains not in response.text
                    ):
                        return self._failure(
                            target,
                            credential_profile,
                            LoginFailureReason.UNEXPECTED_RESPONSE,
                            "HTTP login response did not contain the expected success marker.",
                        )
                    expires_at = self._extract_expires_at(response)
                    cookie_payload = self._cookie_payload(client.cookies)
                    metadata = {
                        "adapter": self.adapter_name,
                        "login_url": self._resolve_url(target.base_url, config.login_request.url),
                        "current_url": self._extract_current_url(response, target.base_url),
                        "cookie_names": list(cookie_payload.keys()),
                        "refresh_supported": config.refresh_request is not None,
                        "role": credential_profile.role,
                    }
                    return LoginExecutionResult(
                        success=True,
                        adapter=self.adapter_name,
                        target_name=target.name,
                        profile_name=credential_profile.name,
                        status=SessionStatus.ACTIVE,
                        session_type=config.session_type,
                        message="HTTP login succeeded.",
                        expires_at=expires_at,
                        refresh_supported=config.refresh_request is not None,
                        session_metadata_redacted=redact_session_metadata(metadata),
                        storage_payload={"cookies": cookie_payload},
                    )
            except TargetPolicyError as error:
                return self._failure(
                    target,
                    credential_profile,
                    LoginFailureReason.CONFIG_ERROR,
                    "HTTP login was blocked by the authenticated target boundary.",
                    last_error=str(error),
                )
            except httpx.HTTPError as error:
                last_exception = error
        return self._failure(
            target,
            credential_profile,
            LoginFailureReason.NETWORK_FAILURE,
            "HTTP login failed due to a network error.",
            last_error=str(last_exception) if last_exception else None,
        )

    def validate(
        self,
        target: Target,
        credential_profile: CredentialProfile,
        config: HttpLoginConfig,
        stored_payload: dict[str, object],
        *,
        before_request: Callable[[], None] | None = None,
    ) -> SessionValidationResult:
        try:
            with self._build_client(
                timeout=config.request_timeout_seconds,
                cookies=stored_payload.get("cookies", {}),
                target=target,
                before_request=before_request,
            ) as client:
                response = self._send_request(
                    client,
                    target,
                    config.validate_request,
                    self._build_template_context(target, credential_profile, ""),
                )
                if response.status_code in {401, 403}:
                    return SessionValidationResult(
                        valid=False,
                        status=SessionStatus.EXPIRED,
                        failure_reason=LoginFailureReason.SESSION_EXPIRED,
                        message="Session expired or was rejected during validation.",
                        last_error=f"HTTP {response.status_code}",
                    )
                if response.status_code != config.validate_request.expected_status:
                    return SessionValidationResult(
                        valid=False,
                        status=SessionStatus.INVALID,
                        failure_reason=LoginFailureReason.UNEXPECTED_RESPONSE,
                        message="Validation returned an unexpected status code.",
                        last_error=f"HTTP {response.status_code}",
                    )
                if (
                    config.validate_request.success_contains
                    and config.validate_request.success_contains not in response.text
                ):
                    return SessionValidationResult(
                        valid=False,
                        status=SessionStatus.INVALID,
                        failure_reason=LoginFailureReason.UNEXPECTED_RESPONSE,
                        message="Validation response did not contain the expected success marker.",
                    )
                return SessionValidationResult(
                    valid=True,
                    status=SessionStatus.ACTIVE,
                    message="Session validation succeeded.",
                    expires_at=self._extract_expires_at(response),
                    session_metadata_redacted=redact_session_metadata(
                        {
                            "current_url": self._extract_current_url(response, target.base_url),
                            "validate_url": self._resolve_url(
                                target.base_url, config.validate_request.url
                            ),
                        }
                    ),
                )
        except TargetPolicyError as error:
            return SessionValidationResult(
                valid=False,
                status=SessionStatus.ERROR,
                failure_reason=LoginFailureReason.CONFIG_ERROR,
                message="Validation was blocked by the authenticated target boundary.",
                last_error=str(error),
            )
        except httpx.HTTPError as error:
            return SessionValidationResult(
                valid=False,
                status=SessionStatus.ERROR,
                failure_reason=LoginFailureReason.NETWORK_FAILURE,
                message="Validation failed due to a network error.",
                last_error=str(error),
            )

    def refresh(
        self,
        target: Target,
        credential_profile: CredentialProfile,
        secret_value: str,
        config: HttpLoginConfig,
        stored_payload: dict[str, object],
        *,
        before_request: Callable[[], None] | None = None,
    ) -> LoginExecutionResult:
        if config.refresh_request is None:
            return self._failure(
                target,
                credential_profile,
                LoginFailureReason.CONFIG_ERROR,
                "HTTP session refresh is not configured.",
            )
        try:
            with self._build_client(
                timeout=config.request_timeout_seconds,
                cookies=stored_payload.get("cookies", {}),
                target=target,
                before_request=before_request,
            ) as client:
                response = self._send_request(
                    client,
                    target,
                    config.refresh_request,
                    self._build_template_context(target, credential_profile, secret_value),
                )
                if response.status_code in {401, 403}:
                    return self._failure(
                        target,
                        credential_profile,
                        LoginFailureReason.SESSION_EXPIRED,
                        "Session refresh was rejected by the target.",
                        status=SessionStatus.EXPIRED,
                    )
                if response.status_code != config.refresh_request.expected_status:
                    return self._failure(
                        target,
                        credential_profile,
                        LoginFailureReason.UNEXPECTED_RESPONSE,
                        "Session refresh returned an unexpected response.",
                    )
                cookie_payload = self._cookie_payload(client.cookies)
                metadata = {
                    "adapter": self.adapter_name,
                    "refresh_url": self._resolve_url(target.base_url, config.refresh_request.url),
                    "current_url": self._extract_current_url(response, target.base_url),
                    "cookie_names": list(cookie_payload.keys()),
                    "role": credential_profile.role,
                }
                return LoginExecutionResult(
                    success=True,
                    adapter=self.adapter_name,
                    target_name=target.name,
                    profile_name=credential_profile.name,
                    status=SessionStatus.REFRESHED,
                    session_type=config.session_type,
                    message="HTTP session refresh succeeded.",
                    expires_at=self._extract_expires_at(response),
                    refresh_supported=True,
                    session_metadata_redacted=redact_session_metadata(metadata),
                    storage_payload={"cookies": cookie_payload},
                )
        except TargetPolicyError as error:
            return self._failure(
                target,
                credential_profile,
                LoginFailureReason.CONFIG_ERROR,
                "Session refresh was blocked by the authenticated target boundary.",
                last_error=str(error),
            )
        except httpx.HTTPError as error:
            return self._failure(
                target,
                credential_profile,
                LoginFailureReason.NETWORK_FAILURE,
                "Session refresh failed due to a network error.",
                last_error=str(error),
            )

    def _build_client(
        self,
        *,
        timeout: int,
        cookies: dict[str, object] | None = None,
        target: Target,
        before_request: Callable[[], None] | None,
    ) -> httpx.Client:
        def guard_request(request: httpx.Request) -> None:
            if before_request is not None:
                before_request()
            self.network_guard.ensure_allowed(target.base_url, str(request.url))

        return httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            cookies=cookies,
            transport=self.transport,
            trust_env=False,
            event_hooks={"request": [guard_request]},
        )

    def _send_request(
        self,
        client: httpx.Client,
        target: Target,
        request_config: HttpRequestConfig,
        context: dict[str, str],
    ) -> httpx.Response:
        rendered_headers = self._render_mapping(request_config.headers, context)
        rendered_body = self._render_mapping(request_config.body, context)
        kwargs: dict[str, Any] = {"headers": rendered_headers}
        if request_config.method == "POST":
            if request_config.body_type == "json":
                kwargs["json"] = rendered_body
            else:
                kwargs["data"] = rendered_body
        response = client.request(
            request_config.method,
            self._resolve_url(target.base_url, request_config.url),
            **kwargs,
        )
        return response

    def _build_template_context(
        self,
        target: Target,
        credential_profile: CredentialProfile,
        secret_value: str,
    ) -> dict[str, str]:
        return {
            "username": credential_profile.username,
            "secret": secret_value,
            "password": secret_value,
            "role": credential_profile.role,
            "target_name": target.name,
        }

    def _render_mapping(self, value: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
        rendered: dict[str, Any] = {}
        for key, item in value.items():
            rendered[key] = self._render_value(item, context)
        return rendered

    def _render_value(self, value: Any, context: dict[str, str]) -> Any:
        if isinstance(value, str):
            return _TEMPLATE_PATTERN.sub(lambda match: context.get(match.group(1), ""), value)
        if isinstance(value, dict):
            return {key: self._render_value(item, context) for key, item in value.items()}
        if isinstance(value, list):
            return [self._render_value(item, context) for item in value]
        return value

    def _resolve_url(self, base_url: str, configured_url: str) -> str:
        return urljoin(f"{base_url.rstrip('/')}/", configured_url.lstrip("/"))

    def _cookie_payload(self, cookies: httpx.Cookies) -> dict[str, str]:
        payload: dict[str, str] = {}
        for cookie in cookies.jar:
            payload[cookie.name] = cookie.value
        return payload

    def _extract_expires_at(self, response: httpx.Response) -> datetime | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        expires_at = payload.get("expires_at")
        if not isinstance(expires_at, str):
            return None
        return datetime.fromisoformat(expires_at)

    def _extract_current_url(self, response: httpx.Response, fallback_url: str) -> str:
        try:
            payload = response.json()
        except ValueError:
            return fallback_url
        current_url = payload.get("current_url")
        if isinstance(current_url, str) and current_url.strip():
            return current_url
        return fallback_url

    def _failure(
        self,
        target: Target,
        credential_profile: CredentialProfile,
        reason: LoginFailureReason,
        message: str,
        *,
        last_error: str | None = None,
        status: SessionStatus = SessionStatus.ERROR,
    ) -> LoginExecutionResult:
        return LoginExecutionResult(
            success=False,
            adapter=self.adapter_name,
            target_name=target.name,
            profile_name=credential_profile.name,
            status=status,
            failure_reason=reason,
            message=message,
            last_error=last_error,
        )
