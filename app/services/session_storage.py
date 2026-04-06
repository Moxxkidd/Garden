"""Persistent storage for non-display-safe session payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.errors import InputValidationError


class SessionStorageService:
    """Store raw session material outside the main database record."""

    def __init__(self, base_directory: Path | None = None) -> None:
        self.base_directory = base_directory or (Path.cwd() / "data" / "session_payloads")
        self.base_directory.mkdir(parents=True, exist_ok=True)

    def write_payload(self, session_id: int, payload: dict[str, Any]) -> str:
        storage_path = self.base_directory / f"session-{session_id}.json"
        storage_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(storage_path)

    def read_payload(self, storage_ref: str) -> dict[str, Any]:
        storage_path = Path(storage_ref)
        if not storage_path.exists():
            raise InputValidationError(f"Session payload '{storage_path}' does not exist.")
        return json.loads(storage_path.read_text(encoding="utf-8"))
