"""Schemas and path helpers for finding/report exports."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class FindingsExportResult(BaseModel):
    format: str
    output_path: str


class JobReportResult(BaseModel):
    job_id: int
    format: str
    output_path: str


class RetestRunSummary(BaseModel):
    finding_id: int
    status: str
    summary: str
    inventory_run_id: int | None = None
    scan_job_id: int | None = None
    session_id: int | None = None
    reused_session: bool = False


def default_findings_export_path(export_format: str) -> Path:
    exports_root = Path.cwd() / "exports" / "findings"
    exports_root.mkdir(parents=True, exist_ok=True)
    suffix = "md" if export_format == "md" else export_format
    return exports_root / f"findings.{suffix}"


def default_job_report_path(job_id: int, export_format: str) -> Path:
    exports_root = Path.cwd() / "exports" / "reports"
    exports_root.mkdir(parents=True, exist_ok=True)
    return exports_root / f"job-{job_id}.{export_format}"
