"""Network admission, preflight, redirect control, and bounded HTTP collection."""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from ipaddress import IPv4Address, IPv6Address, ip_address
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from app.core.errors import InputValidationError, TargetPolicyError
from app.core.settings import Settings
from app.schemas.scan import FetchResult, ScanOptions

MAX_RESPONSE_BYTES = 256 * 1024
TRANSIENT_STATUS_CODES = {502, 503, 504}
TRANSIENT_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


@dataclass(frozen=True)
class NetworkPreflight:
    normalized_url: str
    resolved_addresses: tuple[str, ...]
    proxy_enabled: bool
    policy: str


class FetchError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        url: str,
        retryable: bool = False,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.url = url
        self.retryable = retryable
        self.attempts = attempts


class TargetNetworkPolicy:
    """Resolve and classify every destination before a connection is attempted."""

    def __init__(
        self,
        settings: Settings,
        *,
        resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
    ) -> None:
        self.settings = settings
        self.resolver = resolver

    def normalize_url(self, raw_url: str) -> str:
        value = raw_url.strip()
        if not value:
            raise InputValidationError("URL cannot be blank.")
        try:
            parsed = urlparse(value)
            port = parsed.port
        except ValueError as error:
            raise InputValidationError("URL contains an invalid port.") from error
        if parsed.scheme.lower() not in {"http", "https"}:
            raise InputValidationError("URL scheme must be http or https.")
        if not parsed.hostname:
            raise InputValidationError("URL must include a hostname.")
        if parsed.username is not None or parsed.password is not None:
            raise InputValidationError("Credentials must not be embedded in the URL.")
        host = parsed.hostname.lower().rstrip(".")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise InputValidationError("URL hostname is invalid.") from error
        if port is not None and not 1 <= port <= 65535:
            raise InputValidationError("URL port must be between 1 and 65535.")
        host_display = f"[{host}]" if ":" in host else host
        netloc = f"{host_display}:{port}" if port is not None else host_display
        path = parsed.path or "/"
        return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))

    def preflight(self, raw_url: str) -> NetworkPreflight:
        normalized = self.normalize_url(raw_url)
        addresses = self.ensure_destination_allowed(normalized)
        proxy_enabled = bool(self._proxy_for_url(normalized))
        return NetworkPreflight(
            normalized_url=normalized,
            resolved_addresses=tuple(str(address) for address in addresses),
            proxy_enabled=proxy_enabled,
            policy=self.policy_name,
        )

    def ensure_destination_allowed(self, url: str) -> list[IPv4Address | IPv6Address]:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            raise TargetPolicyError("Destination does not include a hostname.")
        addresses = self._resolve(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        denied = [address for address in addresses if not self._address_allowed(address)]
        if denied:
            rendered = ", ".join(str(address) for address in denied)
            raise TargetPolicyError(
                f"Destination resolves to addresses blocked by {self.policy_name}: {rendered}."
            )
        return addresses

    @property
    def policy_name(self) -> str:
        if self.settings.allow_private_targets and self.settings.allow_non_local_targets:
            return "authorized-private-and-public"
        if self.settings.allow_private_targets:
            return "authorized-private-and-local"
        if self.settings.allow_non_local_targets:
            return "public-and-local"
        return "local-only"

    def proxy_for_url(self, url: str) -> str | None:
        return self._proxy_for_url(url)

    def _resolve(self, host: str, port: int) -> list[IPv4Address | IPv6Address]:
        try:
            direct = ip_address(host)
        except ValueError:
            try:
                records = self.resolver(host, port, type=socket.SOCK_STREAM)
            except OSError as error:
                raise TargetPolicyError(f"DNS preflight failed for '{host}': {error}.") from error
            values = {record[4][0] for record in records if record[4]}
            if not values:
                raise TargetPolicyError(
                    f"DNS preflight returned no addresses for '{host}'."
                ) from None
            return sorted((ip_address(value) for value in values), key=str)
        return [direct]

    def _address_allowed(self, address: IPv4Address | IPv6Address) -> bool:
        if address.is_unspecified or address.is_multicast or address.is_link_local:
            return False
        if address.is_loopback:
            return True
        if address.is_private:
            return self.settings.allow_private_targets
        if not address.is_global:
            return False
        return self.settings.allow_non_local_targets

    def _proxy_for_url(self, url: str) -> str | None:
        proxy = self.settings.scan_proxy_url
        if not proxy:
            return None
        parsed_proxy = urlparse(proxy)
        if parsed_proxy.scheme not in {"http", "https", "socks5", "socks5h"}:
            raise InputValidationError("GARDEN_SCAN_PROXY_URL uses an unsupported scheme.")
        host = (urlparse(url).hostname or "").lower()
        bypass = [item.strip().lower() for item in self.settings.scan_no_proxy.split(",")]
        if any(self._host_matches(host, item) for item in bypass if item):
            return None
        return proxy

    def _host_matches(self, host: str, rule: str) -> bool:
        normalized = rule.lstrip(".")
        return host == normalized or host.endswith(f".{normalized}")


class _HTMLMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self.in_title = True
        if lowered not in {"a", "link", "script", "img", "iframe"}:
            return
        lookup = dict(attrs)
        candidate = lookup.get("href") or lookup.get("src")
        if candidate:
            self.links.append(candidate)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data.strip())


class HttpScanGateway:
    """A passive HTTP gateway with explicit redirects and one bounded retry by default."""

    def __init__(
        self,
        policy: TargetNetworkPolicy,
        *,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.policy = policy
        self.transport = transport
        self.sleeper = sleeper

    def fetch(self, url: str, options: ScanOptions) -> FetchResult:
        started = time.monotonic()
        current = self.policy.normalize_url(url)
        redirects: list[str] = []
        total_attempts = 0
        for redirect_index in range(options.max_redirects + 1):
            self.policy.ensure_destination_allowed(current)
            response, body, attempts = self._request_once_with_retry(current, options)
            total_attempts += attempts
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise FetchError(
                        "redirect_without_location",
                        f"HTTP {response.status_code} response omitted Location.",
                        url=current,
                        attempts=total_attempts,
                    )
                if redirect_index >= options.max_redirects:
                    raise FetchError(
                        "redirect_limit_exceeded",
                        f"Redirect limit of {options.max_redirects} was exceeded.",
                        url=current,
                        attempts=total_attempts,
                    )
                current = self.policy.normalize_url(urljoin(current, location))
                self.policy.ensure_destination_allowed(current)
                redirects.append(current)
                continue
            content_type = response.headers.get("content-type")
            text = self._decode(body, response.encoding)
            title, links = self._parse_html(current, text, content_type)
            return FetchResult(
                requested_url=url,
                final_url=current,
                status_code=response.status_code,
                headers={key.lower(): value for key, value in response.headers.items()},
                content_type=content_type,
                body_text=text,
                title=title,
                discovered_urls=links,
                redirects=redirects,
                attempts=total_attempts,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        raise AssertionError("unreachable redirect state")

    def _request_once_with_retry(
        self, url: str, options: ScanOptions
    ) -> tuple[httpx.Response, bytes, int]:
        retries = options.retry_attempts or 0
        proxy = self.policy.proxy_for_url(url)
        timeout = options.request_timeout_seconds or 5.0
        for attempt in range(1, retries + 2):
            try:
                with httpx.Client(
                    timeout=timeout,
                    follow_redirects=False,
                    trust_env=False,
                    proxy=proxy,
                    transport=self.transport,
                    headers={"User-Agent": options.user_agent, "Accept": "*/*"},
                ) as client:
                    with client.stream("GET", url) as response:
                        chunks: list[bytes] = []
                        size = 0
                        for chunk in response.iter_bytes():
                            remaining = MAX_RESPONSE_BYTES - size
                            if remaining <= 0:
                                break
                            chunks.append(chunk[:remaining])
                            size += min(len(chunk), remaining)
                        body = b"".join(chunks)
                if response.status_code not in TRANSIENT_STATUS_CODES:
                    return response, body, attempt
                if attempt > retries:
                    raise FetchError(
                        "transient_http_exhausted",
                        f"HTTP {response.status_code} persisted after {attempt} attempt(s).",
                        url=url,
                        retryable=True,
                        attempts=attempt,
                    )
            except TRANSIENT_EXCEPTIONS as error:
                if attempt > retries:
                    raise FetchError(
                        "network_retry_exhausted",
                        f"Network request failed after {attempt} attempt(s): {error}",
                        url=url,
                        retryable=True,
                        attempts=attempt,
                    ) from error
            if attempt <= retries:
                self.sleeper(0.2 * (2 ** (attempt - 1)))
        raise AssertionError("unreachable retry state")

    def _decode(self, body: bytes, encoding: str | None) -> str:
        return body.decode(encoding or "utf-8", errors="replace")

    def _parse_html(
        self, base_url: str, text: str, content_type: str | None
    ) -> tuple[str | None, list[str]]:
        if "html" not in (content_type or "").lower():
            return None, []
        parser = _HTMLMetadataParser()
        try:
            parser.feed(text)
        except Exception:
            return None, []
        title = " ".join(part for part in parser.title_parts if part).strip() or None
        normalized: list[str] = []
        seen: set[str] = set()
        for candidate in parser.links:
            joined = urljoin(base_url, candidate)
            parsed = urlparse(joined)
            if parsed.scheme not in {"http", "https"}:
                continue
            value = urlunparse(
                (parsed.scheme, parsed.netloc, parsed.path or "/", "", parsed.query, "")
            )
            if value not in seen:
                seen.add(value)
                normalized.append(value)
        return title, normalized
