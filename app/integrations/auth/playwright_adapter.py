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
                browser = playwright.chromium.launch(headless=True, channel="chromium")
                context = browser.new_context()
                page = context.new_page()
                page.goto(
                    self._resolve_url(target.base_url, config.login_url),
                    timeout=config.request_timeout_seconds * 1000,
                )
                if config.auto_detect_selectors and self._visible_password_count(page) == 0:
                    page.goto(
                        self._resolve_url(target.base_url, "/"),
                        wait_until="domcontentloaded",
                        timeout=config.request_timeout_seconds * 1000,
                    )
                    self._wait_after_submit(page, config.request_timeout_seconds * 1000)
                login_url = page.url
                if config.auto_detect_selectors:
                    detection = self._auto_submit_login(page, username, password)
                else:
                    page.fill(config.username_selector, username)
                    page.fill(config.password_selector, password)
                    page.click(config.submit_selector)
                    detection = {
                        "username_selector": config.username_selector,
                        "password_selector": config.password_selector,
                        "submit_selector": config.submit_selector,
                    }
                self._wait_after_submit(page, config.request_timeout_seconds * 1000)
                if config.success_url_contains:
                    try:
                        page.wait_for_url(
                            f"**{config.success_url_contains}**",
                            timeout=config.request_timeout_seconds * 1000,
                        )
                    except PlaywrightTimeoutError:
                        pass
                if config.success_text:
                    try:
                        page.locator("body").filter(has_text=config.success_text).wait_for(
                            timeout=config.request_timeout_seconds * 1000
                        )
                    except PlaywrightTimeoutError:
                        pass
                if config.success_selector:
                    page.wait_for_selector(
                        config.success_selector, timeout=config.request_timeout_seconds * 1000
                    )
                storage_state = context.storage_state()
                current_url = page.url
                page_text = page.text_content("body") or ""
                password_inputs_count = self._visible_password_count(page)
                browser.close()
                return {
                    "storage_state": storage_state,
                    "login_url": login_url,
                    "current_url": current_url,
                    "page_text": page_text,
                    "password_inputs_count": password_inputs_count,
                    "detected_selectors": detection,
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
                browser = playwright.chromium.launch(headless=True, channel="chromium")
                context = browser.new_context(storage_state=storage_state)
                page = context.new_page()
                page.goto(
                    self._resolve_url(target.base_url, config.validate_url),
                    timeout=config.request_timeout_seconds * 1000,
                )
                self._wait_after_submit(page, config.request_timeout_seconds * 1000)
                if config.success_text:
                    try:
                        page.locator("body").filter(has_text=config.success_text).wait_for(
                            timeout=config.request_timeout_seconds * 1000
                        )
                    except PlaywrightTimeoutError:
                        pass
                if config.success_selector:
                    page.wait_for_selector(
                        config.success_selector, timeout=config.request_timeout_seconds * 1000
                    )
                current_url = page.url
                page_text = page.text_content("body") or ""
                password_inputs_count = self._visible_password_count(page)
                browser.close()
                return {
                    "current_url": current_url,
                    "page_text": page_text,
                    "password_inputs_count": password_inputs_count,
                }
        except PlaywrightTimeoutError as error:
            raise TimeoutError(str(error)) from error

    def _resolve_url(self, base_url: str, configured_url: str) -> str:
        return urljoin(f"{base_url.rstrip('/')}/", configured_url.lstrip("/"))

    def _visible_password_count(self, page) -> int:
        return int(
            page.locator("input[type='password']").evaluate_all(
                """
                elements => elements.filter((element) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== "hidden"
                        && style.display !== "none"
                        && rect.width > 0
                        && rect.height > 0
                        && !element.disabled
                        && !element.readOnly;
                }).length
                """
            )
        )

    def _wait_after_submit(self, page, timeout_ms: int) -> None:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
        except Exception:
            pass

    def _auto_submit_login(self, page, username: str, password: str) -> dict[str, str]:
        result = page.evaluate(
            """
            () => {
                const isVisible = (element) => {
                    if (!element) return false;
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== "hidden"
                        && style.display !== "none"
                        && rect.width > 0
                        && rect.height > 0
                        && !element.disabled
                        && !element.readOnly;
                };
                const describe = (element) => {
                    if (!element) return "";
                    if (element.id) return `#${element.id}`;
                    const name = element.getAttribute("name");
                    if (name) return `${element.tagName.toLowerCase()}[name="${name}"]`;
                    const type = element.getAttribute("type");
                    return `${element.tagName.toLowerCase()}${type ? `[type="${type}"]` : ""}`;
                };
                const allInputs = Array.from(document.querySelectorAll("input"));
                const passwordInput = allInputs.find((input) => {
                    const type = (input.getAttribute("type") || "").toLowerCase();
                    return isVisible(input) && type === "password";
                });
                if (!passwordInput) {
                    return { ok: false, reason: "No visible password field found." };
                }
                const scope = passwordInput.form || passwordInput.closest("form") || document;
                const scopedInputs = Array.from(scope.querySelectorAll("input")).filter(isVisible);
                const passwordIndex = scopedInputs.indexOf(passwordInput);
                const usernameCandidates = scopedInputs.filter((input) => {
                    if (input === passwordInput) return false;
                    const type = (input.getAttribute("type") || "text").toLowerCase();
                    if (!["text", "email", "tel", "search", ""].includes(type)) return false;
                    const haystack = [
                        input.id,
                        input.name,
                        input.autocomplete,
                        input.placeholder,
                        input.getAttribute("aria-label"),
                    ].filter(Boolean).join(" ").toLowerCase();
                    return /user|email|mail|login|account|phone|mobile|name/.test(haystack)
                        || scopedInputs.indexOf(input) < passwordIndex;
                });
                const usernameInput = usernameCandidates[0]
                    || scopedInputs.find((input) => input !== passwordInput);
                if (!usernameInput) {
                    return { ok: false, reason: "No visible username field found." };
                }
                const submitCandidates = Array.from(
                    scope.querySelectorAll('button, input[type="submit"]')
                ).filter(isVisible);
                const scoreSubmit = (element) => {
                    const text = (
                        element.innerText
                        || element.textContent
                        || element.value
                        || element.getAttribute("aria-label")
                        || ""
                    ).trim().toLowerCase();
                    const type = (element.getAttribute("type") || "").toLowerCase();
                    let score = 0;
                    if (/^(log\\s?in|sign\\s?in|submit)$/.test(text)) score += 100;
                    if (/log\\s?in|sign\\s?in/.test(text)) score += 40;
                    if (type === "submit") score += 20;
                    if (/forgot|reset|signup|sign\\s?up|register/.test(text)) score -= 100;
                    return score;
                };
                const submit = submitCandidates
                    .map((element, index) => ({ element, index, score: scoreSubmit(element) }))
                    .sort((left, right) => right.score - left.score || left.index - right.index)[0]
                    ?.element;
                usernameInput.setAttribute("data-garden-autologin", "username");
                passwordInput.setAttribute("data-garden-autologin", "password");
                if (submit && isVisible(submit)) {
                    submit.setAttribute("data-garden-autologin", "submit");
                }
                return {
                    ok: true,
                    username_selector: describe(usernameInput),
                    password_selector: describe(passwordInput),
                    submit_selector: describe(submit),
                };
            }
            """,
        )
        if not isinstance(result, dict) or not result.get("ok"):
            reason = (
                result.get("reason") if isinstance(result, dict) else "Unknown detection error."
            )
            raise TimeoutError(str(reason))
        page.fill("[data-garden-autologin='username']", username)
        page.fill("[data-garden-autologin='password']", password)
        if page.locator("[data-garden-autologin='submit']").count():
            page.click("[data-garden-autologin='submit']")
        else:
            page.press("[data-garden-autologin='password']", "Enter")
        return {
            "username_selector": str(result.get("username_selector") or "auto"),
            "password_selector": str(result.get("password_selector") or "auto"),
            "submit_selector": str(result.get("submit_selector") or "auto"),
        }


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
            "detected_selectors": result.get("detected_selectors"),
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
        lowered_page_text = page_text.lower()
        failure_text_seen = any(
            marker in lowered_page_text
            for marker in (
                "invalid username",
                "invalid password",
                "invalid username or password",
                "incorrect username",
                "incorrect password",
                "login failed",
                "authentication failed",
                "invalid credentials",
            )
        )
        if failure_text_seen:
            return False
        url_ok = not config.success_url_contains or config.success_url_contains in current_url
        text_ok = not config.success_text or config.success_text in page_text
        selector_ok = (
            config.success_selector is None or result.get("storage_state") is not None or page_text
        )
        if config.success_url_contains or config.success_text or config.success_selector:
            return bool(url_ok and text_ok and selector_ok)
        if not config.auto_detect_selectors:
            return bool(url_ok and text_ok and selector_ok)

        login_url = str(result.get("login_url", ""))
        password_inputs_count = int(result.get("password_inputs_count") or 0)
        left_login_page = bool(login_url and current_url and current_url != login_url)
        no_login_form = password_inputs_count == 0
        return bool(url_ok and text_ok and (left_login_page or no_login_form))

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
