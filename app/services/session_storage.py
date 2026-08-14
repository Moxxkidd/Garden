"""Persistent storage for non-display-safe session payloads."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.cli.paths import formal_runtime_paths
from app.core.errors import InputValidationError


class SessionStorageService:
    """Store raw session material outside the main database record."""

    def __init__(self, base_directory: Path | None = None) -> None:
        formal_paths = formal_runtime_paths()
        self.base_directory = base_directory or (
            formal_paths.storage_dir / "session_payloads"
            if formal_paths is not None
            else Path.cwd() / "data" / "session_payloads"
        )
        self.base_directory.mkdir(parents=True, exist_ok=True)

    def write_payload(self, session_id: int, payload: dict[str, Any]) -> str:
        storage_path = self.base_directory / f"session-{session_id}.json"
        storage_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(storage_path)

    def write_request_payload(
        self,
        run_id: int,
        context_id: int,
        payload: dict[str, Any],
    ) -> str:
        storage_directory = self.base_directory / "requests" / f"run-{run_id}"
        storage_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        storage_path = storage_directory / f"context-{context_id}-{uuid4().hex}.json"
        descriptor = os.open(storage_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        return str(storage_path)

    def read_payload(self, storage_ref: str) -> dict[str, Any]:
        storage_path = Path(storage_ref)
        if not storage_path.exists():
            raise InputValidationError(f"Session payload '{storage_path}' does not exist.")
        return json.loads(storage_path.read_text(encoding="utf-8"))
