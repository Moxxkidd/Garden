"""Playwright-backed page screenshot capture for evidence."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from app.core.errors import InputValidationError
from app.models.enums import SessionType


@dataclass(frozen=True)
class PageCaptureArtifact:
    page_title: str | None
    final_url: str
    status_code: int | None
    screenshot_path: str


class PageEvidenceCaptureGatewayProtocol(Protocol):
    def capture_page(
        self,
        *,
        base_url: str,
        url: str,
        session_type: SessionType,
        session_payload: dict[str, Any],
        output_path: Path,
    ) -> PageCaptureArtifact: ...


class SyncPlaywrightEvidenceCaptureGateway:
    """Capture a page screenshot using a restored authenticated browser context."""

    def capture_page(
        self,
        *,
        base_url: str,
        url: str,
        session_type: SessionType,
        session_payload: dict[str, Any],
        output_path: Path,
    ) -> PageCaptureArtifact:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright runtime is not installed in this environment."
            ) from error

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = self._build_context(browser, base_url, session_type, session_payload)
                page = context.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                time.sleep(0.2)
                page.screenshot(path=str(output_path), full_page=True)
                artifact = PageCaptureArtifact(
                    page_title=page.title() or None,
                    final_url=page.url,
                    status_code=response.status if response else None,
                    screenshot_path=str(output_path),
                )
                browser.close()
                return artifact
        except PlaywrightError as error:
            raise RuntimeError(f"Playwright evidence capture failed: {error}") from error

    def _build_context(self, browser, base_url: str, session_type: SessionType, session_payload):
        if session_type == SessionType.PLAYWRIGHT_STORAGE_STATE:
            storage_state = session_payload.get("storage_state")
            if not isinstance(storage_state, dict):
                raise InputValidationError("Playwright session payload is missing storage_state.")
            return browser.new_context(storage_state=storage_state)

        context = browser.new_context()
        if session_type == SessionType.HTTP_COOKIE_JAR:
            cookies = session_payload.get("cookies", {})
            if not isinstance(cookies, dict):
                raise InputValidationError("HTTP cookie jar payload is malformed.")
            parsed = urlparse(base_url)
            context.add_cookies(
                [
                    {
                        "name": name,
                        "value": str(value),
                        "domain": parsed.hostname or "localhost",
                        "path": "/",
                        "httpOnly": True,
                        "secure": parsed.scheme == "https",
                        "sameSite": "Lax",
                    }
                    for name, value in cookies.items()
                ]
            )
        return context
