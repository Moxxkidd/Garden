"""Evidence export helpers."""

from __future__ import annotations

import json

from app.models.evidence import Evidence
from app.models.finding import Finding
from app.schemas.evidence import EvidenceExportResult, default_evidence_export_path
from app.services.evidence_storage import EvidenceStorageService


class EvidenceExportService:
    """Export finding-linked evidence in redacted formats."""

    def __init__(self, *, storage_service: EvidenceStorageService | None = None) -> None:
        self.storage_service = storage_service or EvidenceStorageService()

    def export_json(self, finding: Finding, evidences: list[Evidence]) -> EvidenceExportResult:
        output_path = default_evidence_export_path(finding.id, "json")
        output_path.write_text(
            json.dumps(self._serialize_bundle(finding, evidences), indent=2),
            encoding="utf-8",
        )
        return EvidenceExportResult(
            finding_id=finding.id,
            format="json",
            output_path=str(output_path),
        )

    def export_markdown(self, finding: Finding, evidences: list[Evidence]) -> EvidenceExportResult:
        output_path = default_evidence_export_path(finding.id, "md")
        lines = [
            f"# Evidence Export for Finding {finding.id}",
            "",
            f"Title: {finding.title}",
            f"Category: {finding.category}",
            f"Severity: {finding.severity}",
            f"Confidence: {finding.confidence}",
            "",
            "## Evidence",
        ]
        for evidence in evidences:
            payload = self.storage_service.read_payload(evidence.storage_ref)
            lines.extend(
                [
                    "",
                    f"### Evidence {evidence.id}: {evidence.title}",
                    f"- Type: {evidence.evidence_type}",
                    f"- URL: {evidence.url or '-'}",
                    f"- Method: {evidence.http_method or '-'}",
                    "- Status: "
                    f"{evidence.http_status if evidence.http_status is not None else '-'}",
                    f"- Summary: {evidence.summary}",
                    "",
                    "Redacted Preview:",
                    "",
                    "```text",
                    evidence.preview_redacted,
                    "```",
                    "",
                    "Stored Payload:",
                    "",
                    "```json",
                    json.dumps(payload, indent=2),
                    "```",
                ]
            )
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return EvidenceExportResult(
            finding_id=finding.id,
            format="md",
            output_path=str(output_path),
        )

    def _serialize_bundle(self, finding: Finding, evidences: list[Evidence]) -> dict[str, object]:
        return {
            "finding": {
                "id": finding.id,
                "title": finding.title,
                "category": finding.category,
                "severity": finding.severity,
                "confidence": finding.confidence,
                "status": finding.status,
                "inventory_refs": finding.inventory_refs,
            },
            "evidence": [
                {
                    "id": evidence.id,
                    "evidence_type": evidence.evidence_type,
                    "title": evidence.title,
                    "summary": evidence.summary,
                    "url": evidence.url,
                    "page_title": evidence.page_title,
                    "http_method": evidence.http_method,
                    "http_status": evidence.http_status,
                    "preview_redacted": evidence.preview_redacted,
                    "storage_payload": self.storage_service.read_payload(evidence.storage_ref),
                }
                for evidence in evidences
            ],
        }
