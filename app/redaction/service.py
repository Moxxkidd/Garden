"""Dedicated redaction service for display, evidence, and export flows."""

from __future__ import annotations

import re
from typing import Any


class RedactionService:
    """Redact sensitive values from text and structured metadata."""

    _email_pattern = re.compile(r"([A-Z0-9._%+-])[A-Z0-9._%+-]*(@[A-Z0-9.-]+\.[A-Z]{2,})", re.I)
    _bearer_pattern = re.compile(r"(Bearer\s+)([A-Za-z0-9._\-]{6,})", re.I)
    _cookie_pattern = re.compile(r"(?i)\b(cookie|set-cookie)\s*:\s*([^\n]+)")
    _auth_header_pattern = re.compile(r"(?i)\bauthorization\s*:\s*([^\n]+)")
    _session_assign_pattern = re.compile(
        r"(?i)([\"']?)(session(?:id)?|password|secret|token|api[_-]?key|key)([\"']?\s*[:=]\s*)(['\"]?)([^\"'\s,;}{]+)"
    )
    _token_like_pattern = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")
    _control_character_pattern = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
    _sensitive_key_names = {
        "cookie",
        "set-cookie",
        "authorization",
        "bearer",
        "password",
        "secret",
        "token",
        "session",
        "session_id",
        "apikey",
        "api_key",
        "api-key",
        "key",
    }
    _path_like_keys = {"path", "screenshot_path", "storage_ref"}
    _safe_http_metadata_headers = {
        "accept-ranges",
        "cache-control",
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "date",
        "etag",
        "expires",
        "last-modified",
        "permissions-policy",
        "referrer-policy",
        "server",
        "strict-transport-security",
        "transfer-encoding",
        "vary",
        "x-content-type-options",
        "x-frame-options",
    }

    def redact_text(self, value: str | None, *, limit: int = 400) -> str:
        return self._redact_text(value, limit=limit, mask_token_like=True)

    def _redact_text(
        self,
        value: str | None,
        *,
        limit: int,
        mask_token_like: bool,
    ) -> str:
        if not value:
            return ""
        redacted = self._control_character_pattern.sub(" ", value)
        redacted = self._email_pattern.sub(self._mask_email, redacted)
        redacted = self._bearer_pattern.sub(r"\1***", redacted)
        redacted = self._cookie_pattern.sub(lambda match: f"{match.group(1)}: ***", redacted)
        redacted = self._auth_header_pattern.sub("Authorization: ***", redacted)
        redacted = self._session_assign_pattern.sub(
            lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}{match.group(4)}***",
            redacted,
        )
        if mask_token_like:
            redacted = self._token_like_pattern.sub(self._mask_token_like, redacted)
        redacted = " ".join(redacted.split())
        if len(redacted) <= limit:
            return redacted
        return f"{redacted[:limit]}..."

    def redact_mapping(self, value: dict[str, Any], *, limit: int = 400) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if self._is_sensitive_key(key):
                redacted[key] = "***"
                continue
            if lowered in self._path_like_keys and isinstance(item, str):
                redacted[key] = item
                continue
            if "email" in lowered and isinstance(item, str):
                redacted[key] = self.redact_text(item, limit=limit)
                continue
            if isinstance(item, dict):
                redacted[key] = self.redact_mapping(item, limit=limit)
                continue
            if isinstance(item, list):
                redacted[key] = [self._redact_list_entry(entry, limit=limit) for entry in item]
                continue
            redacted[key] = self._redact_scalar(item, limit=limit)
        return redacted

    def redact_http_headers(self, value: dict[str, str], *, limit: int = 400) -> dict[str, str]:
        redacted: dict[str, str] = {}
        for key, item in value.items():
            lowered = key.lower()
            if self._is_sensitive_key(key):
                redacted[key] = "***"
            elif lowered in self._safe_http_metadata_headers:
                redacted[key] = self._redact_text(
                    item,
                    limit=limit,
                    mask_token_like=False,
                )
            else:
                redacted[key] = self.redact_text(item, limit=limit)
        return redacted

    def _redact_scalar(self, value: Any, *, limit: int) -> Any:
        if isinstance(value, str):
            return self.redact_text(value, limit=limit)
        return value

    def _redact_list_entry(self, value: Any, *, limit: int) -> Any:
        if isinstance(value, dict):
            return self.redact_mapping(value, limit=limit)
        if isinstance(value, list):
            return [self._redact_list_entry(entry, limit=limit) for entry in value]
        return self._redact_scalar(value, limit=limit)

    def _is_sensitive_key(self, key: str) -> bool:
        lowered = key.lower().replace("-", "_")
        return lowered in {name.replace("-", "_") for name in self._sensitive_key_names}

    def _mask_email(self, match: re.Match[str]) -> str:
        return f"{match.group(1)}***{match.group(2)}"

    def _mask_token_like(self, match: re.Match[str]) -> str:
        value = match.group(0)
        if value.isdigit():
            return value
        return f"{value[:3]}***{value[-3:]}"
