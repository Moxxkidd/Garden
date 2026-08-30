"""Inventory schemas and controls."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.errors import InputValidationError


class InventoryBuildControls(BaseModel):
    max_pages: int = Field(default=25, ge=1, le=100)
    max_depth: int = Field(default=2, ge=0, le=5)
    max_requests: int = Field(default=100, ge=0, le=2000)
    delay_ms: int = Field(default=250, ge=0, le=5000)
    request_timeout_seconds: float = Field(default=15, gt=0, le=60)
    retry_attempts: int = Field(default=0, ge=0, le=2)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    @field_validator("include", "exclude")
    @classmethod
    def validate_path_rules(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            rule = value.strip()
            if not rule:
                continue
            if not rule.startswith("/"):
                raise InputValidationError("Include/exclude rules must start with '/'.")
            cleaned.append(rule.rstrip("/") or "/")
        return cleaned


class InventoryCounts(BaseModel):
    pages: int = 0
    endpoints: int = 0
    parameters: int = 0
    annotations: int = 0


class InventoryBuildSummary(BaseModel):
    inventory_run_id: int
    scan_job_id: int
    target_name: str
    profile_name: str
    auth_session_id: int
    status: str
    counts: InventoryCounts
    summary: str


class InventoryExportResult(BaseModel):
    inventory_run_id: int
    format: Literal["json", "csv"]
    output_path: str


def default_inventory_export_path(run_id: int, export_format: Literal["json", "csv"]) -> Path:
    base_directory = Path.cwd() / "exports"
    if export_format == "json":
        return base_directory / "inventory" / f"inventory-{run_id}.json"
    return base_directory / f"inventory-{run_id}"
