"""Playwright UI login adapter."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from app.models.credential_profile import CredentialProfile
from app.models.enums import LoginFailureReason, SessionStatus, SessionType
from app.models.target import Target
from app.redaction.display import redact_session_metadata
from app.schemas.auth import (
    LoginExecutionResult,
    PlaywrightLoginConfig,
    SessionValidationResult,
)


class PlaywrightGatewayProtocol:
    def login(
        self, config: PlaywrightLoginConfig, target: Target, username: str, password: str
    ) -> dict[str, Any]:
        raise NotImplementedError

    def validate(
        self,
        config: PlaywrightLoginConfig,
        target: Target,
        storage_state: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError


class SyncPlaywrightGateway(PlaywrightGatewayProtocol):
    def login(
        self, config: PlaywrightLoginConfig, target: Target, username: str, password: str
    ) -> dict[str, Any]:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright runtime is not installed in this environment."
            ) from error

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                page.goto(
                    self._resolve_url(target.base_url, config.login_url),
                    timeout=config.request_timeout_seconds * 1000,
                )
                page.fill(config.username_selector, username)
                page.fill(config.password_selector, password)
                page.click(config.submit_selector)
                if config.success_selector:
                    page.wait_for_selector(
                        config.success_selector, timeout=config.request_timeout_seconds * 1000
                    )
                storage_state = context.storage_state()
                current_url = page.url
                page_text = page.text_content("body") or ""
                browser.close()
                return {
                    "storage_state": storage_state,
                    "current_url": current_url,
                    "page_text": page_text,
                }
        except PlaywrightTimeoutError as error:
            raise TimeoutError(str(error)) from error

    def validate(
        self,
        config: PlaywrightLoginConfig,
        target: Target,
        storage_state: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright runtime is not installed in this environment."
            ) from error

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(storage_state=storage_state)
                page = context.new_page()
                page.goto(
                    self._resolve_url(target.base_url, config.validate_url),
                    timeout=config.request_timeout_seconds * 1000,
                )
                if config.success_selector:
                    page.wait_for_selector(
                        config.success_selector, timeout=config.request_timeout_seconds * 1000
                    )
                current_url = page.url
                page_text = page.text_content("body") or ""
                browser.close()
                return {"current_url": current_url, "page_text": page_text}
        except PlaywrightTimeoutError as error:
            raise TimeoutError(str(error)) from error

    def _resolve_url(self, base_url: str, configured_url: str) -> str:
        return urljoin(f"{base_url.rstrip('/')}/", configured_url.lstrip("/"))


class PlaywrightLoginAdapter:
    adapter_name = "playwright"

    def __init__(self, gateway: PlaywrightGatewayProtocol | None = None) -> None:
        self.gateway = gateway or SyncPlaywrightGateway()

    def login(
        self,
        target: Target,
        credential_profile: CredentialProfile,
        secret_value: str,
        config: PlaywrightLoginConfig,
    ) -> LoginExecutionResult:
        try:
            result = self.gateway.login(config, target, credential_profile.username, secret_value)
        except TimeoutError as error:
            return self._failure(
                target,
                credential_profile,
                LoginFailureReason.FORM_NOT_FOUND,
                "Playwright login could not find the expected form or success selector.",
                last_error=str(error),
            )
        except RuntimeError as error:
            return self._failure(
                target,
                credential_profile,
                LoginFailureReason.CONFIG_ERROR,
                str(error),
                last_error=str(error),
            )

        if not self._is_success(config, result):
            return self._failure(
                target,
                credential_profile,
                LoginFailureReason.LOGIN_REJECTED,
                "Playwright login did not reach the expected authenticated state.",
            )

        metadata = {
            "adapter": self.adapter_name,
            "login_url": self._resolve_url(target.base_url, config.login_url),
            "validate_url": self._resolve_url(target.base_url, config.validate_url),
            "current_url": result.get("current_url"),
            "origins": [
                item.get("origin")
                for item in result.get("storage_state", {}).get("origins", [])
                if isinstance(item, dict)
            ],
        }
        return LoginExecutionResult(
            success=True,
            adapter=self.adapter_name,
            target_name=target.name,
            profile_name=credential_profile.name,
            status=SessionStatus.ACTIVE,
            session_type=SessionType.PLAYWRIGHT_STORAGE_STATE,
            message="Playwright UI login succeeded.",
            refresh_supported=config.refresh_via_relogin,
            session_metadata_redacted=redact_session_metadata(metadata),
            storage_payload={"storage_state": result["storage_state"]},
        )

    def validate(
        self,
        target: Target,
        credential_profile: CredentialProfile,
        config: PlaywrightLoginConfig,
        stored_payload: dict[str, object],
    ) -> SessionValidationResult:
        try:
            result = self.gateway.validate(
                config,
                target,
                stored_payload.get("storage_state", {}),
            )
        except TimeoutError as error:
            return SessionValidationResult(
                valid=False,
                status=SessionStatus.EXPIRED,
                failure_reason=LoginFailureReason.SESSION_EXPIRED,
                message="Playwright session no longer reaches the expected authenticated page.",
                last_error=str(error),
            )
        except RuntimeError as error:
            return SessionValidationResult(
                valid=False,
                status=SessionStatus.ERROR,
                failure_reason=LoginFailureReason.CONFIG_ERROR,
                message=str(error),
                last_error=str(error),
            )
        if not self._is_success(config, result):
            return SessionValidationResult(
                valid=False,
                status=SessionStatus.EXPIRED,
                failure_reason=LoginFailureReason.SESSION_EXPIRED,
                message=(
                    "Playwright session validation did not reach the expected authenticated state."
                ),
            )
        return SessionValidationResult(
            valid=True,
            status=SessionStatus.ACTIVE,
            message="Playwright session validation succeeded.",
            session_metadata_redacted=redact_session_metadata(
                {
                    "validate_url": self._resolve_url(target.base_url, config.validate_url),
                    "current_url": result.get("current_url"),
                }
            ),
        )

    def refresh(
        self,
        target: Target,
        credential_profile: CredentialProfile,
        secret_value: str,
        config: PlaywrightLoginConfig,
        stored_payload: dict[str, object],
    ) -> LoginExecutionResult:
        if not config.refresh_via_relogin:
            return self._failure(
                target,
                credential_profile,
                LoginFailureReason.CONFIG_ERROR,
                "Playwright refresh is not configured.",
            )
        return self.login(target, credential_profile, secret_value, config)

    def _is_success(self, config: PlaywrightLoginConfig, result: dict[str, Any]) -> bool:
        current_url = str(result.get("current_url", ""))
        page_text = str(result.get("page_text", ""))
        url_ok = not config.success_url_contains or config.success_url_contains in current_url
        text_ok = not config.success_text or config.success_text in page_text
        selector_ok = (
            config.success_selector is None or result.get("storage_state") is not None or page_text
        )
        return bool(url_ok and text_ok and selector_ok)

    def _resolve_url(self, base_url: str, configured_url: str) -> str:
        return urljoin(f"{base_url.rstrip('/')}/", configured_url.lstrip("/"))

    def _failure(
        self,
        target: Target,
        credential_profile: CredentialProfile,
        reason: LoginFailureReason,
        message: str,
        *,
        last_error: str | None = None,
    ) -> LoginExecutionResult:
        return LoginExecutionResult(
            success=False,
            adapter=self.adapter_name,
            target_name=target.name,
            profile_name=credential_profile.name,
            status=SessionStatus.ERROR,
            failure_reason=reason,
            message=message,
            last_error=last_error,
        )
