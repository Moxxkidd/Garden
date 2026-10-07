"""Bounded browser discovery with optional isolated identity state."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from hashlib import sha256
from time import monotonic
from urllib.parse import urlsplit

from app.core.errors import InputValidationError, OverallScanTimeout, TargetPolicyError
from app.schemas.scan import FetchResult, ScanOptions
from app.services.authenticated_network import AuthenticatedNetworkGuard
from app.services.discovery_html import parse_html
from app.services.scan_network import TargetNetworkPolicy


class BrowserDiscovery:
    def __init__(self, policy: TargetNetworkPolicy) -> None:
        self.policy = policy
        self.guard = AuthenticatedNetworkGuard(policy)

    def _route_url(self, url: str) -> str:
        normalized = self.policy.normalize_url(url)
        fragment = urlsplit(url).fragment
        return normalized + ("#" + fragment if fragment.startswith(("/", "!/")) else "")

    def collect(
        self,
        start_url: str,
        options: ScanOptions,
        before_request: Callable[[], None],
        on_response: Callable[[FetchResult, str | None], None],
        on_candidate: Callable[[dict], None],
        seed_urls: list[str] | None = None,
        on_attempt: Callable[[str, str], None] | None = None,
        *,
        identity_state=None,
        response_candidates=None,
        before_send=None,
        on_checkpoint=None,
        allowed_urls=None,
        recovery_work=None,
        on_state_provider=None,
        on_finish=None,
    ) -> dict:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        base = self.guard.ensure_allowed(start_url, start_url)
        deadline = monotonic() + (options.overall_timeout_seconds or 300)
        attempts = resources = candidate_count = document_requests = 0
        stopped: str | None = None
        errors: list[Exception] = []
        queue = deque([(self._route_url(start_url), 0, "GET")])
        queued = {(queue[0][0], "GET")}
        document_method = "GET"
        route_window: str | None = None
        pages = 0
        request_routes: dict[object, str | None] = {}
        pending_responses: dict[object, object] = {}
        redirect_depths: dict[str, int] = {}

        def check() -> None:
            if errors:
                raise errors[0]
            if monotonic() >= deadline:
                raise OverallScanTimeout("Anonymous browser exceeded overall deadline.")
            before_request()

        def candidate(item: dict) -> bool:
            nonlocal candidate_count, stopped
            if candidate_count >= options.max_candidates:
                stopped = stopped or "max_candidates"
                return False
            candidate_count += 1
            on_candidate(item)
            return True

        def enqueue(url: str, depth: int) -> None:
            nonlocal stopped
            try:
                normalized = self._route_url(url)
                self.guard.ensure_allowed(base, normalized)
            except (TargetPolicyError, InputValidationError, ValueError):
                return
            if allowed_urls is not None and normalized not in allowed_urls:
                return
            if (normalized, "GET") in queued:
                return
            if depth > options.max_depth:
                stopped = stopped or "max_depth"
                return
            if len(queued) >= options.max_candidates:
                stopped = stopped or "max_candidates"
                return
            queued.add((normalized, "GET"))
            queue.append((normalized, depth, "GET"))

        for seed in seed_urls or []:
            enqueue(seed, 0)

        if recovery_work:
            queue = deque(
                (self._route_url(item["url"]), 0, item["method"]) for item in recovery_work
            )
            queued = {(url, method) for url, _, method in queue}

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True, channel="chromium")
            except PlaywrightError as error:
                raise RuntimeError(
                    "Chromium could not start. Install it with: "
                    "python -m playwright install chromium"
                ) from error
            context = None
            try:
                context = browser.new_context(
                    service_workers="block",
                    user_agent=options.user_agent,
                    accept_downloads=False,
                    storage_state=identity_state.storage_state if identity_state else None,
                )
                # Worker traffic has no reliable page attribution. Block worker creation;
                # the anonymous mode observes page/frame HTTP requests only.
                context.add_init_script("""
                    for (const key of ['Worker', 'SharedWorker']) {
                        Object.defineProperty(globalThis, key, {value: class {
                            constructor() { throw new Error('Anonymous discovery blocks workers'); }
                        }, configurable: false});
                    }
                    window.open = () => null;
                """)
                context.route_web_socket("**/*", lambda socket: socket.close())
                page = context.new_page()
                if identity_state:
                    from app.services.identity_sessions import install_session_storage

                    install_session_storage(context, identity_state, page)
                cdp = context.new_cdp_session(page)

                live_local = {}
                live_session = {}
                if identity_state:
                    for item in identity_state.storage_state["origins"]:
                        live_local[item["origin"]] = {
                            entry["name"]: entry["value"] for entry in item["localStorage"]
                        }
                    live_session = {
                        site: dict(values)
                        for site, values in identity_state.session_storage.items()
                    }

                    def storage_changed(event, action):
                        storage_id = event["storageId"]
                        site = storage_id.get("securityOrigin")
                        if site != identity_state.target_origin:
                            return
                        stores = live_local if storage_id["isLocalStorage"] else live_session
                        values = stores.setdefault(site, {})
                        if action == "clear":
                            values.clear()
                        elif action == "remove":
                            values.pop(event["key"], None)
                        else:
                            values[event["key"]] = event["newValue"]

                    for event_name, action in (
                        ("domStorageItemsCleared", "clear"),
                        ("domStorageItemRemoved", "remove"),
                        ("domStorageItemAdded", "set"),
                        ("domStorageItemUpdated", "set"),
                    ):
                        cdp.on(
                            "DOMStorage." + event_name,
                            lambda event, action=action: storage_changed(event, action),
                        )
                    cdp.send("DOMStorage.enable")

                def current_identity_state():
                    if identity_state is None:
                        return None
                    # Storage events track mutations even during navigation. Querying
                    # the departing document here can block a paused request.
                    cookies = context.cookies()
                    stored = {
                        "cookies": cookies,
                        "origins": [
                            {
                                "origin": site,
                                "localStorage": [
                                    {"name": name, "value": value} for name, value in values.items()
                                ],
                            }
                            for site, values in live_local.items()
                        ],
                    }
                    return identity_state.model_copy(
                        update={
                            "storage_state": stored,
                            "session_storage": {
                                site: dict(values) for site, values in live_session.items()
                            },
                        }
                    )

                if on_state_provider:
                    on_state_provider(current_identity_state)

                def guard_request(event: dict) -> None:
                    nonlocal attempts, resources, stopped, document_requests
                    request = event["request"]
                    url, method = request["url"], request["method"].upper()
                    kind = event.get("resourceType", "")
                    if recovery_work and kind == "Document" and route_window:
                        method = document_method
                    previous = event.get("redirectedRequestId")
                    redirects = redirect_depths.get(previous, 0) + 1 if previous else 0
                    redirect_depths[event["requestId"]] = redirects
                    allowed = True
                    reason = None
                    try:
                        check()
                        self.guard.ensure_allowed(base, url)
                        if allowed_urls is not None and self.policy.normalize_url(url) not in {
                            self.policy.normalize_url(x) for x in allowed_urls
                        }:
                            allowed, reason = False, "outside_recovery_scope"
                        elif redirects > options.max_redirects:
                            allowed, reason = False, "max_redirects"
                            stopped = stopped or reason
                        elif method not in {"GET", "HEAD"}:
                            allowed, reason = False, "non_readonly_method"
                        elif stopped in {"max_browser_requests", "max_candidates"}:
                            allowed, reason = False, stopped
                        elif attempts >= options.max_browser_requests:
                            stopped = reason = "max_browser_requests"
                            allowed = False
                        elif kind == "Document" and document_requests >= options.max_pages:
                            stopped = stopped or "max_pages"
                            allowed, reason = False, "max_pages"
                        elif kind != "Document" and resources >= options.max_resources:
                            stopped = stopped or "max_resources"
                            allowed, reason = False, "max_resources"
                    except (TargetPolicyError, InputValidationError, ValueError):
                        allowed, reason = False, "out_of_scope"
                    except Exception as error:
                        errors.append(error)
                        allowed = False
                    try:
                        recorded = candidate(
                            {
                                "url": url,
                                "asset_type": "page"
                                if kind == "Document"
                                else ("api" if kind in {"XHR", "Fetch"} else "resource"),
                                "method": method,
                                "source_kind": "browser_request",
                                "source_url": None,
                                "route_url": None,
                                "auto_visit": allowed,
                                "skipped_reason": reason,
                            }
                        )
                        if allowed and recorded and not errors:
                            if before_send:
                                before_send()
                            attempts += 1
                            if on_attempt is not None:
                                on_attempt(url, method)
                            if kind == "Document":
                                document_requests += 1
                            if kind != "Document":
                                resources += 1
                            cdp.send(
                                "Fetch.continueRequest",
                                {"requestId": event["requestId"], "method": method},
                            )
                        else:
                            cdp.send(
                                "Fetch.failRequest",
                                {"requestId": event["requestId"], "errorReason": "BlockedByClient"},
                            )
                    except Exception as error:
                        # Navigation can cancel a paused request while health proof
                        # yields to Chromium. There is then nothing left to resume;
                        # keep its attempted budget charge and unobserved candidate.
                        if isinstance(error, PlaywrightError) and (
                            "Invalid InterceptionId" in str(error)
                        ):
                            return
                        errors.append(error)
                        try:
                            cdp.send(
                                "Fetch.failRequest",
                                {"requestId": event["requestId"], "errorReason": "BlockedByClient"},
                            )
                        except PlaywrightError:
                            pass

                cdp.on("Fetch.requestPaused", guard_request)
                cdp.send(
                    "Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}
                )

                def context_guard(route) -> None:
                    try:
                        if route.request.frame.page != page:
                            route.abort()
                            return
                        self.guard.ensure_allowed(base, route.request.url)
                        if route.request.method not in {"GET", "HEAD"}:
                            route.abort()
                            return
                    except (PlaywrightError, TargetPolicyError, InputValidationError):
                        route.abort()
                        return
                    route.continue_()

                context.route("**/*", context_guard)
                page.on(
                    "request", lambda request: request_routes.__setitem__(request, route_window)
                )

                def response_observed(response, *, finished: bool) -> None:
                    try:
                        self.guard.ensure_allowed(base, response.url)
                        headers = response.all_headers()
                        content_type = headers.get("content-type", "")
                        is_text = any(
                            token in content_type.lower()
                            for token in ("text/", "json", "javascript", "xml")
                        )
                        raw_length = headers.get("content-length", "")
                        length = int(raw_length) if raw_length.isdigit() else None
                        bounded = (
                            length is not None
                            and 0 <= length <= 256 * 1024
                            and not headers.get("content-encoding")
                            and not headers.get("transfer-encoding")
                        )
                        body = b""
                        complete = False
                        if bounded and is_text and finished:
                            try:
                                body = response.body()
                                complete = len(body) == length and len(body) <= 256 * 1024
                            except PlaywrightError:
                                pass
                        if not complete:
                            body = b""
                        result = FetchResult(
                            requested_url=response.request.url,
                            final_url=response.url,
                            method=(
                                document_method
                                if recovery_work and response.request.resource_type == "document"
                                else response.request.method
                            ),
                            route_url=request_routes.get(response.request),
                            status_code=response.status,
                            headers=headers,
                            content_type=content_type,
                            body_text=body.decode("utf-8", errors="replace"),
                            body_size_bytes=len(body) if complete else (length or 0),
                            body_is_text=is_text,
                            body_truncated=not complete,
                            body_sha256=sha256(body).hexdigest() if complete else "",
                        )
                        on_response(result, request_routes.pop(response.request, None))
                        if response_candidates:
                            for item in response_candidates(result):
                                if candidate(item) and item.get("auto_visit", True):
                                    enqueue(item.get("route_url") or item["url"], 0)
                    except (TargetPolicyError, InputValidationError):
                        pass
                    except Exception as error:
                        errors.append(error)

                def request_finished(request) -> None:
                    response = pending_responses.pop(request, None)
                    if response is not None:
                        response_observed(response, finished=True)
                    else:
                        request_routes.pop(request, None)

                page.on(
                    "response",
                    lambda response: pending_responses.__setitem__(response.request, response),
                )
                page.on("requestfinished", request_finished)
                while queue and stopped not in {"max_browser_requests", "max_candidates"}:
                    check()
                    if pages >= options.max_pages:
                        stopped = stopped or "max_pages"
                        break
                    url, depth, document_method = queue.popleft()
                    route_window = url
                    pages += 1
                    timeout = min(
                        options.request_timeout_seconds or 15, max(0.001, deadline - monotonic())
                    )
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                        remaining = options.render_wait_ms
                        while remaining > 0 and stopped not in {
                            "max_browser_requests",
                            "max_candidates",
                        }:
                            check()
                            step = min(remaining, 50)
                            page.wait_for_timeout(step)
                            remaining -= step
                        check()
                        if urlsplit(page.url).fragment.startswith(("/", "!/")):
                            candidate(
                                {
                                    "url": self.policy.normalize_url(page.url),
                                    "asset_type": "page",
                                    "method": "GET",
                                    "source_kind": "hash_route",
                                    "route_url": page.url,
                                    "route_observed": True,
                                    "auto_visit": False,
                                }
                            )
                        if document_method == "HEAD":
                            continue
                        _, items = parse_html(
                            page.url, page.content(), limit=options.max_candidates
                        )
                        if len(items) >= options.max_candidates:
                            stopped = stopped or "max_candidates"
                        for item in items:
                            if item["source_kind"] == "hash_route":
                                item["auto_visit"] = True
                            if not candidate(item):
                                break
                            if item["auto_visit"] and item["asset_type"] == "page":
                                enqueue(item.get("route_url") or item["url"], depth + 1)
                    except PlaywrightError:
                        check()
                        if document_method != "HEAD":
                            stopped = stopped or "navigation_error"
                    finally:
                        route_window = None
                check()
                for response in list(pending_responses.values()):
                    response_observed(response, finished=False)
                check()
                if on_finish:
                    on_finish()
            finally:
                if on_checkpoint:
                    on_checkpoint(
                        [
                            {"url": url, "depth": depth, "method": method}
                            for url, depth, method in queue
                        ]
                    )
                if context is not None:
                    context.close()
                browser.close()
        return {"request_attempts": attempts, "stopped_reason": stopped}
