"""Finding export helpers."""

from __future__ import annotations

import csv
import json

from app.models.finding import Finding
from app.schemas.reporting import FindingsExportResult, default_findings_export_path


class FindingExportService:
    """Export findings for review, tickets, and demos."""

    def export_markdown(
        self,
        findings: list[Finding],
        *,
        status_breakdown: dict[str, int],
    ) -> FindingsExportResult:
        output_path = default_findings_export_path("md")
        lines = [
            "# Garden Findings Export",
            "",
            "## Status Breakdown",
            "",
        ]
        for status, count in status_breakdown.items():
            lines.append(f"- {status}: {count}")
        lines.extend(["", "## Findings"])
        for finding in findings:
            profile_label = (
                finding.credential_profile.name
                if finding.credential_profile
                else finding.credential_profile_id
            )
            lines.extend(
                [
                    "",
                    f"### Finding {finding.id}: {finding.title}",
                    f"- Status: {finding.status}",
                    f"- Severity: {finding.severity}",
                    f"- Confidence: {finding.confidence}",
                    f"- Category: {finding.category}",
                    f"- Target: {finding.target.name if finding.target else finding.target_id}",
                    f"- Profile: {profile_label}",
                    f"- First Seen: {finding.first_seen.isoformat()}",
                    f"- Last Seen: {finding.last_seen.isoformat()}",
                    f"- Evidence Count: {len(finding.evidences)}",
                    "",
                    "Trigger:",
                    finding.trigger_explanation,
                    "",
                    "Reproduction Notes:",
                    finding.reproduction_notes,
                    "",
                    "Remediation Notes:",
                    finding.remediation_notes,
                ]
            )
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return FindingsExportResult(format="md", output_path=str(output_path))

    def export_json(
        self,
        findings: list[Finding],
        *,
        status_breakdown: dict[str, int],
    ) -> FindingsExportResult:
        output_path = default_findings_export_path("json")
        payload = {
            "status_breakdown": status_breakdown,
            "findings": [self._serialize_finding(finding) for finding in findings],
        }
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return FindingsExportResult(format="json", output_path=str(output_path))

    def export_csv(self, findings: list[Finding]) -> FindingsExportResult:
        output_path = default_findings_export_path("csv")
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "id",
                    "title",
                    "status",
                    "severity",
                    "confidence",
                    "category",
                    "check_name",
                    "target",
                    "profile",
                    "first_seen",
                    "last_seen",
                    "evidence_count",
                    "location_key",
                ],
            )
            writer.writeheader()
            for finding in findings:
                writer.writerow(
                    {
                        "id": finding.id,
                        "title": finding.title,
                        "status": finding.status,
                        "severity": finding.severity,
                        "confidence": finding.confidence,
                        "category": finding.category,
                        "check_name": finding.check_name,
                        "target": finding.target.name if finding.target else finding.target_id,
                        "profile": (
                            finding.credential_profile.name
                            if finding.credential_profile
                            else finding.credential_profile_id
                        ),
                        "first_seen": finding.first_seen.isoformat(),
                        "last_seen": finding.last_seen.isoformat(),
                        "evidence_count": len(finding.evidences),
                        "location_key": finding.location_key or "",
                    }
                )
        return FindingsExportResult(format="csv", output_path=str(output_path))

    def _serialize_finding(self, finding: Finding) -> dict[str, object]:
        return {
            "id": finding.id,
            "title": finding.title,
            "status": finding.status,
            "severity": finding.severity,
            "confidence": finding.confidence,
            "category": finding.category,
            "check_name": finding.check_name,
            "target": finding.target.name if finding.target else finding.target_id,
            "profile": (
                finding.credential_profile.name
                if finding.credential_profile
                else finding.credential_profile_id
            ),
            "first_seen": finding.first_seen.isoformat(),
            "last_seen": finding.last_seen.isoformat(),
            "location_key": finding.location_key,
            "inventory_refs": finding.inventory_refs,
            "evidence_refs": finding.evidence_refs,
            "reproduction_notes": finding.reproduction_notes,
            "remediation_notes": finding.remediation_notes,
        }
