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
        self.base_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.base_directory.chmod(0o700)

    def write_payload(self, session_id: int, payload: dict[str, Any]) -> str:
        storage_path = self.base_directory / f"session-{session_id}-{uuid4().hex}.json"
        descriptor = os.open(storage_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            storage_path.unlink(missing_ok=True)
            raise
        return str(storage_path)

    def read_identity_payload(self, storage_ref: str) -> dict[str, Any]:
        path = Path(storage_ref)
        try:
            if path.is_symlink() or path.resolve().parent != self.base_directory.resolve():
                raise ValueError
            if path.stat().st_size > 2 * 1048576:
                raise ValueError
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise InputValidationError("受保护登录状态不可用，请重新认证。") from None

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
