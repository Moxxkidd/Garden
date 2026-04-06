"""Display-safe redaction helpers."""

from __future__ import annotations

from typing import Any

from app.redaction.service import RedactionService

_service = RedactionService()


def redact_secret_ref(secret_ref: str) -> str:
    if secret_ref.startswith("literal://"):
        return "literal://***"
    return secret_ref


def redact_session_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return _service.redact_mapping(metadata)
