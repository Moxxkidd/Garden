"""Evidence transport schemas."""

from pathlib import Path

from pydantic import BaseModel


class EvidenceExportResult(BaseModel):
    finding_id: int
    format: str
    output_path: str


def default_evidence_export_path(finding_id: int, export_format: str) -> Path:
    exports_root = Path.cwd() / "exports" / "evidence"
    exports_root.mkdir(parents=True, exist_ok=True)
    suffix = "json" if export_format == "json" else "md"
    return exports_root / f"finding-{finding_id}.{suffix}"
