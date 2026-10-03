"""Bounded, network-free parsers for explicitly supplied discovery sources.

URLs are exact request material. Callers must apply origin/network policy before
visiting, and redact them before rendering or persisting public provenance.
"""

import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

from app.core.errors import InputValidationError

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
_METHODS = frozenset({"get", "head", "post", "put", "patch", "delete", "options", "trace"})


def _invalid(message: str = "Invalid or unsupported discovery input.") -> InputValidationError:
    return InputValidationError(message)


def _validate_text(text: str, maximum: int, limit: int) -> None:
    if type(limit) is not int or limit < 1 or not isinstance(text, str):
        raise _invalid()
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise _invalid() from None
    if size > maximum:
        raise _invalid("Discovery input exceeds the size limit.")


def _url(value: str) -> str:
    if not isinstance(value, str):
        raise _invalid()
    value = value.strip()
    if any(ord(c) < 33 or ord(c) == 127 for c in value) or "\\" in value:
        raise _invalid()
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port
        if ":" in host:
            host = f"[{host}]"
        if port is not None and port != {"http": 80, "https": 443}[parsed.scheme]:
            host = f"{host}:{port}"
        return urlunsplit((parsed.scheme, host, parsed.path or "/", parsed.query, ""))
    except (ValueError, UnicodeError):
        raise _invalid() from None


def _append(items: list, value: object, limit: int) -> None:
    if len(items) >= limit:
        raise _invalid("Discovery input exceeds the item limit.")
    items.append(value)


def parse_sitemap(url: str, body: str, limit: int = 2000) -> tuple[list[dict], list[str]]:
    """Parse one URL set/index; traversal and origin policy belong to the caller."""
    _validate_text(body, 256 * 1024, limit)
    source_url = _url(url)
    if urlsplit(source_url).path.lower().endswith(".gz"):
        raise _invalid("Compressed sitemaps are not supported.")
    upper = body.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise _invalid("DTD and entities are not supported.")
    try:
        root = ElementTree.fromstring(body)
    except (ElementTree.ParseError, ValueError):
        raise _invalid("Malformed sitemap XML.") from None
    namespace = f"{{{_SITEMAP_NS}}}" if root.tag.startswith(f"{{{_SITEMAP_NS}}}") else ""
    kind = root.tag.removeprefix(namespace)
    if kind not in {"urlset", "sitemapindex"}:
        raise _invalid("Unsupported sitemap document.")
    candidates: list[dict] = []
    indexes: list[str] = []
    entry_tag = "url" if kind == "urlset" else "sitemap"
    for entry in root.findall(f"{namespace}{entry_tag}"):
        locations = entry.findall(f"{namespace}loc")
        if len(locations) != 1 or not locations[0].text or len(locations[0]):
            raise _invalid("Malformed sitemap location.")
        target = _url(locations[0].text)
        if kind == "sitemapindex":
            _append(indexes, target, limit)
        else:
            _append(
                candidates,
                dict(
                    url=target,
                    asset_type="page",
                    method="GET",
                    source_kind="sitemap",
                    source_url=source_url,
                    auto_visit=True,
                ),
                limit,
            )
    return candidates, indexes


def _check_refs(document: dict) -> None:
    # Iteration avoids recursion on deeply nested, untrusted documents.
    stack = [document]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if "$ref" in item and (
                not isinstance(item["$ref"], str) or not item["$ref"].startswith("#/")
            ):
                raise _invalid("External OpenAPI references are not supported.")
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def _servers(raw: object, base_url: str | None) -> list[str]:
    if not isinstance(raw, list):
        raise _invalid()
    servers = []
    for server in raw or [{"url": "/"}]:
        if not isinstance(server, dict) or not isinstance(server.get("url"), str):
            raise _invalid()
        value = server["url"]
        if "{" in value or "}" in value:
            raise _invalid("OpenAPI server templates are not supported.")
        if not value.strip():
            raise _invalid()
        try:
            absolute = bool(urlsplit(value).scheme)
        except ValueError:
            raise _invalid() from None
        if not absolute:
            if base_url is None:
                raise _invalid("Relative OpenAPI servers require a base URL.")
            value = urljoin(_url(base_url), value)
        normalized = _url(value)
        if urlsplit(normalized).query or urlsplit(value).fragment:
            raise _invalid()
        servers.append(normalized.rstrip("/"))
    return servers


def _openapi(text: str, limit: int, base_url: str | None) -> list[dict]:
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        raise _invalid("OpenAPI input must be valid JSON.") from None
    if not isinstance(document, dict) or not isinstance(document.get("openapi"), str):
        raise _invalid("Only OpenAPI 3.x JSON is supported.")
    if not re.fullmatch(r"3\.[0-9]+\.[0-9]+", document["openapi"]):
        raise _invalid("Only OpenAPI 3.x JSON is supported.")
    _check_refs(document)
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise _invalid()
    root_servers = document.get("servers", [])
    items: list[dict] = []
    for path, entry in paths.items():
        if path.startswith("x-"):
            continue
        if (
            not path.startswith("/")
            or "?" in path
            or "#" in path
            or not isinstance(entry, dict)
            or "$ref" in entry
        ):
            raise _invalid("Unsupported OpenAPI path declaration.")
        for method, operation in entry.items():
            if method not in _METHODS:
                continue
            if not isinstance(operation, dict):
                raise _invalid()
            servers = _servers(
                operation.get("servers", entry.get("servers", root_servers)), base_url
            )
            for server in servers:
                _append(
                    items,
                    dict(
                        url=_url(server + path),
                        asset_type="api",
                        method=method.upper(),
                        source_kind="openapi_import",
                        auto_visit=False,
                        source_index=len(items) + 1,
                    ),
                    limit,
                )
    return items


def parse_seed_input(
    text: str,
    format: str = "urls",
    limit: int = 1000,
    base_url: str | None = None,
) -> list[dict]:
    """Parse UTF-8 URL lines or OpenAPI JSON without opening files or fetching refs.

    The limit applies to source entries (including duplicates), not unique URLs.
    OpenAPI operations are always declaration-only; no parameters are generated.
    """
    _validate_text(text, 1024 * 1024, limit)
    text = text.removeprefix("\ufeff")
    if format == "openapi":
        return _openapi(text, limit, base_url)
    if format != "urls":
        raise _invalid("Unsupported seed input format.")
    items: list[dict] = []
    for index, line in enumerate(text.split("\n"), 1):
        if line.strip():
            _append(
                items,
                dict(
                    url=_url(line),
                    asset_type="page",
                    method="GET",
                    source_kind="url_import",
                    auto_visit=True,
                    source_index=index,
                ),
                limit,
            )
    return items
