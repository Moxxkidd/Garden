"""Playwright-backed inventory collection gateway."""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

from app.core.errors import InputValidationError, TargetPolicyError
from app.core.settings import get_settings
from app.models.enums import SessionType
from app.models.target import Target
from app.schemas.inventory import InventoryBuildControls
from app.services.authenticated_network import AuthenticatedNetworkGuard
from app.services.scan_network import TargetNetworkPolicy


@dataclass
class ObservedEndpoint:
    method: str
    url: str
    path: str
    status_code: int
    observed_at: datetime
    cache_control: str | None = None
    content_type: str | None = None
    response_preview: str | None = None
    response_text: str | None = None
    set_cookie_names: list[str] = field(default_factory=list)
    cookie_issue_flags: list[str] = field(default_factory=list)
    parameters: dict[str, list[str]] = field(default_factory=dict)
    request_url: str | None = None
    request_headers: dict[str, str] = field(default_factory=dict)
    request_body: bytes | None = None
    response_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ObservedPage:
    url: str
    title: str
    visited_at: datetime
    depth: int
    discovered_urls: list[str]
    status_code: int | None = None
    cache_control: str | None = None
    content_type: str | None = None
    response_text: str | None = None
    text_preview: str | None = None


@dataclass
class InventoryCollectionResult:
    start_url: str
    pages: list[ObservedPage]
    endpoints: list[ObservedEndpoint]


class PlaywrightInventoryGatewayProtocol(Protocol):
    def collect(
        self,
        target: Target,
        start_url: str,
        controls: InventoryBuildControls,
        session_type: SessionType,
        session_payload: dict[str, Any],
        before_request: Callable[[], None] | None = None,
    ) -> InventoryCollectionResult: ...


class SyncPlaywrightInventoryGateway:
    """Collect inventory using Playwright navigation and network observation."""

    DOWNLOAD_SUFFIXES = (
        ".pdf",
        ".zip",
        ".tar",
        ".tar.gz",
        ".tgz",
        ".gz",
        ".bz2",
        ".7z",
        ".rar",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".csv",
    )

    def __init__(self, *, policy: TargetNetworkPolicy | None = None) -> None:
        self.network_guard = AuthenticatedNetworkGuard(
            policy or TargetNetworkPolicy(get_settings())
        )

    def collect(
        self,
        target: Target,
        start_url: str,
        controls: InventoryBuildControls,
        session_type: SessionType,
        session_payload: dict[str, Any],
        before_request: Callable[[], None] | None = None,
    ) -> InventoryCollectionResult:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright runtime is not installed in this environment."
            ) from error

        pages: list[ObservedPage] = []
        endpoints: list[ObservedEndpoint] = []
        queued: deque[tuple[str, int]] = deque([(self._normalize_page_url(start_url), 0)])
        seen_page_urls: set[str] = set()
        queued_urls = {self._normalize_page_url(start_url)}
        request_counter = 0
        origin = self._origin(target.base_url)

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, channel="chromium")
                context = self._build_context(
                    browser,
                    target.base_url,
                    session_type,
                    session_payload,
                )
                page = context.new_page()
                violations = self._install_request_guard(
                    context,
                    page,
                    target.base_url,
                    before_request=before_request,
                )

                def handle_response(response) -> None:
                    nonlocal request_counter
                    request = response.request
                    if request_counter >= controls.max_requests:
                        return
                    if request.resource_type not in {"fetch", "xhr"}:
                        return
                    if self._origin(request.url) != origin:
                        return
                    if not self._path_allowed(request.url, controls.include, controls.exclude):
                        return
                    request_counter += 1
                    set_cookie_names, cookie_issue_flags = self._extract_cookie_metadata(response)
                    content_type = response.header_value("content-type")
                    try:
                        request_body = request.post_data_buffer
                    except Exception:
                        request_body = None
                    try:
                        response_headers = response.all_headers()
                    except Exception:
                        response_headers = {}
                    response_text = self._extract_response_text(response, content_type)
                    endpoints.append(
                        ObservedEndpoint(
                            method=request.method.upper(),
                            url=self._normalize_endpoint_url(request.url),
                            request_url=request.url,
                            path=urlparse(request.url).path or "/",
                            status_code=response.status,
                            observed_at=datetime.now(timezone.utc),
                            cache_control=response.header_value("cache-control"),
                            content_type=content_type,
                            response_preview=(
                                self._normalize_preview(response_text) if response_text else None
                            ),
                            response_text=response_text,
                            set_cookie_names=set_cookie_names,
                            cookie_issue_flags=cookie_issue_flags,
                            parameters=self._extract_parameters(request),
                            request_headers=dict(request.headers),
                            request_body=request_body,
                            response_headers=response_headers,
                        )
                    )

                page.on("response", handle_response)

                while queued and len(seen_page_urls) < controls.max_pages:
                    if before_request is not None:
                        before_request()
                    current_url, depth = queued.popleft()
                    if current_url in seen_page_urls:
                        continue
                    if not self._path_allowed(current_url, controls.include, controls.exclude):
                        continue
                    if self._looks_like_download_url(current_url):
                        continue
                    try:
                        response = None
                        for attempt in range(controls.retry_attempts + 1):
                            try:
                                response = self._guarded_goto(
                                    page,
                                    target.base_url,
                                    current_url,
                                    int(controls.request_timeout_seconds * 1000),
                                    violations,
                                )
                                break
                            except TargetPolicyError:
                                raise
                            except PlaywrightError:
                                if attempt >= controls.retry_attempts:
                                    raise
                                if before_request is not None:
                                    before_request()
                    except PlaywrightError as error:
                        if self._is_download_navigation_error(error):
                            continue
                        raise
                    if controls.delay_ms:
                        time.sleep(controls.delay_ms / 1000)
                    final_url = self._normalize_page_url(page.url)
                    seen_page_urls.add(final_url)
                    title = page.title()
                    discovered_urls = self._extract_links(page, origin, controls)
                    pages.append(
                        ObservedPage(
                            url=final_url,
                            title=title,
                            visited_at=datetime.now(timezone.utc),
                            depth=depth,
                            discovered_urls=discovered_urls,
                            status_code=response.status if response else None,
                            cache_control=(
                                response.header_value("cache-control") if response else None
                            ),
                            content_type=(
                                response.header_value("content-type") if response else None
                            ),
                            response_text=self._extract_navigation_response_text(response),
                            text_preview=self._extract_page_preview(page),
                        )
                    )
                    if depth >= controls.max_depth:
                        continue
                    for discovered_url in discovered_urls:
                        if discovered_url in seen_page_urls or discovered_url in queued_urls:
                            continue
                        if len(seen_page_urls) + len(queued_urls) >= controls.max_pages + 1:
                            break
                        queued.append((discovered_url, depth + 1))
                        queued_urls.add(discovered_url)

                browser.close()
        except PlaywrightError as error:
            raise RuntimeError(f"Playwright inventory collection failed: {error}") from error

        return InventoryCollectionResult(
            start_url=self._normalize_page_url(start_url),
            pages=pages,
            endpoints=endpoints,
        )

    def _install_request_guard(
        self,
        context,
        page,
        base_url: str,
        *,
        before_request: Callable[[], None] | None,
    ) -> list[TargetPolicyError]:
        violations: list[TargetPolicyError] = []
        cdp = context.new_cdp_session(page)

        def guard_request(event: dict[str, Any]) -> None:
            request_id = str(event["requestId"])
            try:
                if before_request is not None:
                    before_request()
                self.network_guard.ensure_allowed(base_url, str(event["request"]["url"]))
            except TargetPolicyError as error:
                violations.append(error)
                cdp.send(
                    "Fetch.failRequest",
                    {"requestId": request_id, "errorReason": "BlockedByClient"},
                )
                return
            cdp.send("Fetch.continueRequest", {"requestId": request_id})

        cdp.on("Fetch.requestPaused", guard_request)
        cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
        return violations

    def _guarded_goto(
        self,
        page,
        base_url: str,
        url: str,
        timeout_ms: int,
        violations: list[TargetPolicyError],
    ):
        self.network_guard.ensure_allowed(base_url, url)
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as error:
            if violations:
                raise violations[0] from error
            raise
        if violations:
            raise violations[0]
        self.network_guard.ensure_allowed(base_url, page.url)
        return response

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

    def _extract_links(self, page, origin: str, controls: InventoryBuildControls) -> list[str]:
        hrefs = page.eval_on_selector_all(
            "a[href]",
            "(elements) => elements.map((element) => element.href)",
        )
        discovered: list[str] = []
        for href in hrefs:
            if not isinstance(href, str):
                continue
            if self._origin(href) != origin:
                continue
            normalized = self._normalize_page_url(href)
            if self._looks_like_download_url(normalized):
                continue
            if self._path_allowed(normalized, controls.include, controls.exclude):
                discovered.append(normalized)
        return sorted(set(discovered))

    def _extract_parameters(self, request) -> dict[str, list[str]]:
        parsed = urlparse(request.url)
        parameters: dict[str, list[str]] = {}
        query_names = sorted(parse_qs(parsed.query, keep_blank_values=True).keys())
        if query_names:
            parameters["query"] = query_names
        post_data = request.post_data
        if not post_data:
            return parameters
        content_type = request.header_value("content-type") or ""
        if "application/json" in content_type:
            try:
                payload = json.loads(post_data)
            except json.JSONDecodeError:
                return parameters
            if isinstance(payload, dict):
                parameters["body"] = sorted(str(key) for key in payload.keys())
            return parameters
        if "application/x-www-form-urlencoded" in content_type:
            parameters["form"] = sorted(parse_qs(post_data, keep_blank_values=True).keys())
        return parameters

    def _normalize_page_url(self, value: str) -> str:
        parsed = urlparse(value)
        normalized = parsed._replace(fragment="")
        return urlunparse(normalized)

    def _normalize_endpoint_url(self, value: str) -> str:
        parsed = urlparse(value)
        normalized = parsed._replace(query="", fragment="")
        return urlunparse(normalized)

    def _origin(self, value: str) -> str:
        parsed = urlparse(value)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _path_allowed(self, value: str, include: list[str], exclude: list[str]) -> bool:
        path = urlparse(value).path or "/"
        normalized_path = path.rstrip("/") or "/"
        if any(normalized_path.startswith(rule) for rule in exclude):
            return False
        if include:
            return any(normalized_path.startswith(rule) for rule in include)
        return True

    def resolve_url(self, base_url: str, configured_url: str) -> str:
        return urljoin(f"{base_url.rstrip('/')}/", configured_url.lstrip("/"))

    def _extract_page_preview(self, page) -> str | None:
        try:
            preview = page.locator("body").inner_text(timeout=2000)
        except Exception:
            return None
        return self._normalize_preview(preview)

    def _extract_navigation_response_text(self, response) -> str | None:
        if response is None:
            return None
        content_type = (response.header_value("content-type") or "").lower()
        if not any(token in content_type for token in ("json", "text", "html", "xml")):
            return None
        try:
            return response.text()[: 256 * 1024]
        except BaseException:
            return None

    def _extract_response_text(self, response, content_type: str | None) -> str | None:
        normalized_type = (content_type or "").lower()
        if not any(token in normalized_type for token in ("json", "text", "html", "xml")):
            return None
        try:
            return response.text()[: 256 * 1024]
        except BaseException:
            return None

    def _extract_cookie_metadata(self, response) -> tuple[list[str], list[str]]:
        raw_value = response.header_value("set-cookie") or ""
        if not raw_value:
            return [], []
        cookie_name = raw_value.split("=", maxsplit=1)[0].strip()
        lowered = raw_value.lower()
        issues: list[str] = []
        if "httponly" not in lowered:
            issues.append("missing_httponly")
        if "secure" not in lowered:
            issues.append("missing_secure")
        if "samesite" not in lowered:
            issues.append("missing_samesite")
        return ([cookie_name] if cookie_name else []), issues

    def _normalize_preview(self, value: str) -> str | None:
        normalized = " ".join(value.split())
        if not normalized:
            return None
        return normalized[:900]

    def _looks_like_download_url(self, value: str) -> bool:
        path = (urlparse(value).path or "").lower()
        return path.endswith(self.DOWNLOAD_SUFFIXES)

    def _is_download_navigation_error(self, error: Exception) -> bool:
        return "download is starting" in str(error).lower()
