"""统一验证运行中的资产身份和稳定响应指纹。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

_DYNAMIC_JSON_KEYS = {
    "csrf",
    "csrftoken",
    "generated",
    "generatedat",
    "nonce",
    "requestid",
    "session",
    "sessionid",
    "timestamp",
    "traceid",
}
_ISO_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:?\d{2})?\b"
)
_DYNAMIC_ASSIGNMENT = re.compile(
    r"(?i)\b(generated(?:_at)?|nonce|request[_-]?id|session(?:[_-]?id)?|trace[_-]?id)"
    r"(\s*[:=]\s*)([^\s,;}&]+)"
)
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.I,
)
_PERCENT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def canonical_asset_identity(method: str, url: str) -> str:
    """Return a value-independent identity for one observed HTTP asset."""

    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("资产身份只接受绝对 HTTP(S) URL。")
    normalized_method = method.strip().upper()
    if not normalized_method:
        raise ValueError("资产身份需要 HTTP 方法。")
    path = _normalize_path(parsed.path or "/")
    path = path.rstrip("/") or "/"
    parameter_names = sorted(
        {name for name, _value in parse_qsl(parsed.query, keep_blank_values=True)}
    )
    suffix = (
        f"?{'&'.join(quote(name, safe='') for name in parameter_names)}" if parameter_names else ""
    )
    return f"{normalized_method}:{path}{suffix}"


def stable_response_signature(text: str, content_type: str | None) -> str:
    """Hash response content after removing common per-request dynamic values."""

    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    normalized: str
    if media_type == "application/json" or media_type.endswith("+json"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            normalized = _normalize_dynamic_text(text)
        else:
            normalized = json.dumps(
                _normalize_json(payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
    else:
        normalized = _normalize_dynamic_text(text)
    material = f"{media_type}\n{normalized}".encode("utf-8", errors="replace")
    return hashlib.sha256(material).hexdigest()


def redacted_observed_url(raw_url: str) -> str:
    """Normalize an observed URL while retaining names but not credential or query values."""

    parsed = urlsplit(raw_url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parsed.path or "/"
    query_names = sorted(name for name, _value in parse_qsl(parsed.query, keep_blank_values=True))
    query = "&".join(f"{quote(name, safe='')}=[REDACTED]" for name in query_names)
    return urlunsplit((scheme, host, path, query, ""))


def _normalize_json(value: Any) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            normalized[str(key)] = (
                "<dynamic>" if normalized_key in _DYNAMIC_JSON_KEYS else _normalize_json(item)
            )
        return normalized
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    if isinstance(value, str):
        return _normalize_dynamic_text(value)
    return value


def _normalize_dynamic_text(value: str) -> str:
    normalized = _ISO_TIMESTAMP.sub("<timestamp>", value)
    normalized = _UUID.sub("<uuid>", normalized)
    normalized = _DYNAMIC_ASSIGNMENT.sub(
        lambda match: f"{match.group(1).lower()}{match.group(2)}<dynamic>",
        normalized,
    )
    return " ".join(normalized.split())


def _normalize_path(value: str) -> str:
    def normalize_escape(match: re.Match[str]) -> str:
        decoded = chr(int(match.group(1), 16))
        return decoded if decoded in _UNRESERVED else f"%{match.group(1).upper()}"

    normalized = _PERCENT_ESCAPE.sub(normalize_escape, value)
    return quote(normalized, safe="/%:@!$&'()*+,;=-._~")
