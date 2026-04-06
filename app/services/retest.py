"""Finding retest orchestration."""

from __future__ import annotations

from app.core.errors import ConflictError
from app.models.enums import AuditEventStatus, AuditEventType, FindingStatus, RetestStatus
from app.schemas.inventory import InventoryBuildControls
from app.schemas.reporting import RetestRunSummary
from app.services.audit import AuditService
from app.services.credentials import CredentialProfileService
from app.services.findings import CheckRunService, FindingService
from app.services.inventory import InventoryBuildService
from app.services.sessions import AuthSessionService
from app.services.targets import TargetService


class RetestService:
    """Retest findings by reusing session context when possible and re-running one check."""

    def __init__(
        self,
        *,
        finding_service: FindingService | None = None,
        session_service: AuthSessionService | None = None,
        inventory_service: InventoryBuildService | None = None,
        check_service: CheckRunService | None = None,
        target_service: TargetService | None = None,
        credential_service: CredentialProfileService | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.finding_service = finding_service or FindingService()
        self.session_service = session_service or AuthSessionService()
        self.inventory_service = inventory_service or InventoryBuildService()
        self.check_service = check_service or CheckRunService(
            inventory_service=self.inventory_service,
            session_service=self.session_service,
            finding_service=self.finding_service,
        )
        self.target_service = target_service or TargetService()
        self.credential_service = credential_service or CredentialProfileService()
        self.audit_service = audit_service or AuditService()

    def run(self, session, *, finding_id: int) -> RetestRunSummary:
        finding = self.finding_service.get(session, finding_id)
        if finding.status not in {
            FindingStatus.FIXED.value,
            FindingStatus.RETEST_PENDING.value,
        }:
            raise ConflictError(
                "Retest is only allowed for findings in fixed or retest-pending state."
            )

        finding.status = FindingStatus.RETEST_PENDING.value
        previous_last_seen = finding.last_seen
        controls = InventoryBuildControls()
        reused_session = False

        validation_result = self.session_service.validate(session, finding.session_id)
        if validation_result.valid:
            inventory_summary = self.inventory_service.build_from_session(
                session,
                finding.session_id,
                controls,
            )
            reused_session = True
        else:
            target = self.target_service.get(session, finding.target_id)
            credential = self.credential_service.get(session, finding.credential_profile_id)
            inventory_summary = self.inventory_service.build_from_target_profile(
                session,
                target.name,
                credential.name,
                controls,
            )

        check_summary = self.check_service.run_inventory_checks(
            session,
            inventory_summary.inventory_run_id,
            selected_check_names={finding.check_name},
        )
        session.refresh(finding)

        if finding.last_seen > previous_last_seen:
            finding.status = FindingStatus.TRIAGED.value
            result_status = RetestStatus.FAILED.value
            summary = (
                f"Finding {finding.id} still reproduced during retest against "
                f"inventory {inventory_summary.inventory_run_id}."
            )
        else:
            finding.status = FindingStatus.CLOSED.value
            result_status = RetestStatus.PASSED.value
            summary = (
                f"Finding {finding.id} was not reproduced during retest against "
                f"inventory {inventory_summary.inventory_run_id}."
            )

        retest_record = self.finding_service.create_retest_record(
            session,
            finding=finding,
            status=result_status,
            summary=summary,
            inventory_run_id=inventory_summary.inventory_run_id,
            session_id=inventory_summary.auth_session_id,
            scan_job_id=check_summary.scan_job_id,
        )
        self.audit_service.record(
            session,
            AuditEventType.RETEST_RUN,
            AuditEventStatus.SUCCESS,
            {
                "finding_id": finding.id,
                "reused_session": reused_session,
                "inventory_run_id": inventory_summary.inventory_run_id,
                "scan_job_id": check_summary.scan_job_id,
                "status": result_status,
                "summary": summary,
            },
            target_id=finding.target_id,
            credential_profile_id=finding.credential_profile_id,
            auth_session_id=inventory_summary.auth_session_id,
            scan_job_id=check_summary.scan_job_id,
        )
        session.flush()
        return RetestRunSummary(
            finding_id=finding.id,
            status=retest_record.status,
            summary=summary,
            inventory_run_id=retest_record.inventory_run_id,
            scan_job_id=retest_record.scan_job_id,
            session_id=retest_record.session_id,
            reused_session=reused_session,
        )
