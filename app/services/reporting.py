"""Workflow report helpers."""

from __future__ import annotations

from app.core.errors import InputValidationError
from app.models.enums import AuditEventStatus, AuditEventType
from app.schemas.reporting import JobReportResult, default_job_report_path
from app.services.audit import AuditService
from app.services.findings import FindingService
from app.services.inventory import InventoryBuildService
from app.services.jobs import ScanJobService


class ReportService:
    """Generate concise redacted job reports."""

    def __init__(
        self,
        *,
        job_service: ScanJobService | None = None,
        finding_service: FindingService | None = None,
        inventory_service: InventoryBuildService | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.job_service = job_service or ScanJobService()
        self.finding_service = finding_service or FindingService()
        self.inventory_service = inventory_service or InventoryBuildService()
        self.audit_service = audit_service or AuditService()

    def generate_job_markdown(self, session, job_id: int) -> JobReportResult:
        job = self.job_service.get(session, job_id)
        findings = self.finding_service.list(session, job_id=job_id)
        inventory_run = self.inventory_service.get_by_job(session, job_id)
        output_path = default_job_report_path(job_id, "md")
        lines = [
            f"# Garden Job Report {job.id}",
            "",
            f"- Status: {job.status}",
            f"- Target ID: {job.target_id}",
            f"- Credential Profile ID: {job.credential_profile_id}",
            f"- Started: {job.started_at.isoformat() if job.started_at else '-'}",
            f"- Finished: {job.finished_at.isoformat() if job.finished_at else '-'}",
            f"- Summary: {job.summary or '-'}",
        ]
        if inventory_run is not None:
            lines.extend(
                [
                    "",
                    "## Inventory Context",
                    "",
                    f"- Inventory Run: {inventory_run.id}",
                    f"- Pages: {inventory_run.pages_count}",
                    f"- Endpoints: {inventory_run.endpoints_count}",
                    f"- Parameters: {inventory_run.parameters_count}",
                    f"- Annotations: {inventory_run.annotations_count}",
                ]
            )
        lines.extend(["", "## Findings"])
        if not findings:
            lines.extend(["", "No findings are currently linked to this job."])
        for finding in findings:
            lines.extend(
                [
                    "",
                    f"### Finding {finding.id}: {finding.title}",
                    f"- Status: {finding.status}",
                    f"- Severity: {finding.severity}",
                    f"- Confidence: {finding.confidence}",
                    f"- Category: {finding.category}",
                    f"- Evidence Count: {len(finding.evidences)}",
                    "",
                    finding.reproduction_notes,
                ]
            )
        output_path.write_text("\n".join(lines), encoding="utf-8")
        self.audit_service.record(
            session,
            AuditEventType.REPORT_EXPORT,
            AuditEventStatus.SUCCESS,
            {
                "job_id": job.id,
                "format": "md",
                "output_path": str(output_path),
                "finding_count": len(findings),
            },
            target_id=job.target_id,
            credential_profile_id=job.credential_profile_id,
            scan_job_id=job.id,
        )
        return JobReportResult(job_id=job_id, format="md", output_path=str(output_path))

    def generate(self, session, *, job_id: int, export_format: str) -> JobReportResult:
        if export_format != "md":
            raise InputValidationError("Job reports currently support only markdown export.")
        return self.generate_job_markdown(session, job_id)
