"""Structured storage for evidence metadata and screenshot files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.errors import InputValidationError


class EvidenceStorageService:
    """Store structured evidence payloads separately from screenshot assets."""

    def __init__(self, base_directory: Path | None = None) -> None:
        self.base_directory = base_directory or (Path.cwd() / "data" / "evidence")
        self.base_directory.mkdir(parents=True, exist_ok=True)
        self.screenshot_directory = self.base_directory / "screenshots"
        self.screenshot_directory.mkdir(parents=True, exist_ok=True)

    def payload_path(self, evidence_id: int) -> Path:
        return self.base_directory / f"evidence-{evidence_id}.json"

    def screenshot_path(self, evidence_id: int) -> Path:
        return self.screenshot_directory / f"evidence-{evidence_id}.png"

    def write_payload(self, evidence_id: int, payload: dict[str, Any]) -> str:
        path = self.payload_path(evidence_id)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(path)

    def read_payload(self, storage_ref: str) -> dict[str, Any]:
        path = Path(storage_ref)
        if not path.exists():
            raise InputValidationError(f"Evidence payload '{path}' does not exist.")
        return json.loads(path.read_text(encoding="utf-8"))
