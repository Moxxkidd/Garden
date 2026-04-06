"""Checks execution and finding lifecycle services."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha1
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import InputValidationError, ResourceNotFoundError
from app.models.enums import AuditEventStatus, AuditEventType, FindingStatus, JobStatus
from app.models.finding import Finding
from app.models.finding_retest import FindingRetestRun
from app.schemas.finding import CheckRunSummary
from app.schemas.job import ScanJobCreate
from app.security_checks.base import CheckContext, CheckFindingCandidate, CheckMetadata
from app.security_checks.registry import get_registered_checks
from app.services.audit import AuditService
from app.services.credentials import CredentialProfileService
from app.services.evidence import EvidenceService
from app.services.inventory import InventoryBuildService
from app.services.jobs import ScanJobService
from app.services.sessions import AuthSessionService
from app.services.targets import TargetService

LEGACY_STATUS_MAP = {
    "open": FindingStatus.NEW.value,
    "resolved": FindingStatus.CLOSED.value,
}

ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    FindingStatus.NEW.value: {
        FindingStatus.TRIAGED.value,
        FindingStatus.ACCEPTED.value,
        FindingStatus.FIXED.value,
    },
    FindingStatus.TRIAGED.value: {
        FindingStatus.ACCEPTED.value,
        FindingStatus.FIXED.value,
        FindingStatus.CLOSED.value,
    },
    FindingStatus.ACCEPTED.value: {
        FindingStatus.FIXED.value,
        FindingStatus.CLOSED.value,
    },
    FindingStatus.FIXED.value: {
        FindingStatus.RETEST_PENDING.value,
    },
    FindingStatus.RETEST_PENDING.value: {
        FindingStatus.TRIAGED.value,
        FindingStatus.ACCEPTED.value,
        FindingStatus.CLOSED.value,
        FindingStatus.FIXED.value,
    },
    FindingStatus.CLOSED.value: {
        FindingStatus.TRIAGED.value,
    },
}


class FindingService:
    """Persist, deduplicate, and transition low-risk findings."""

    def __init__(self, *, audit_service: AuditService | None = None) -> None:
        self.audit_service = audit_service or AuditService()

    def list(
        self,
        session: Session,
        *,
        status: str | None = None,
        category: str | None = None,
        job_id: int | None = None,
    ) -> list[Finding]:
        statement = (
            select(Finding)
            .options(
                selectinload(Finding.target),
                selectinload(Finding.credential_profile),
                selectinload(Finding.auth_session),
                selectinload(Finding.scan_job),
                selectinload(Finding.evidences),
                selectinload(Finding.retests),
            )
            .order_by(Finding.last_seen.desc(), Finding.id.desc())
        )
        if status:
            statement = statement.where(Finding.status == self._canonical_status(status))
        if category:
            statement = statement.where(Finding.category == category)
        if job_id is not None:
            statement = statement.where(Finding.job_id == job_id)
        findings = list(session.scalars(statement))
        self._normalize_statuses(session, findings)
        return findings

    def get(self, session: Session, finding_id: int) -> Finding:
        statement = (
            select(Finding)
            .where(Finding.id == finding_id)
            .options(
                selectinload(Finding.target),
                selectinload(Finding.credential_profile),
                selectinload(Finding.auth_session),
                selectinload(Finding.scan_job),
                selectinload(Finding.evidences),
                selectinload(Finding.retests),
            )
        )
        finding = session.scalar(statement)
        if finding is None:
            raise ResourceNotFoundError(f"Finding {finding_id} was not found.")
        self._normalize_status(session, finding)
        return finding

    def status_breakdown(self, session: Session) -> dict[str, int]:
        findings = self.list(session)
        counts = Counter(finding.status for finding in findings)
        return {status.value: counts.get(status.value, 0) for status in FindingStatus}

    def list_statuses(self) -> list[FindingStatus]:
        return list(FindingStatus)

    def update_status(self, session: Session, finding_id: int, new_status: str) -> Finding:
        finding = self.get(session, finding_id)
        previous_status = finding.status
        canonical_new_status = self._canonical_status(new_status)
        if canonical_new_status == finding.status:
            return finding
        allowed = ALLOWED_STATUS_TRANSITIONS.get(finding.status, set())
        if canonical_new_status not in allowed:
            raise InputValidationError(
                f"Finding {finding.id} cannot move from {finding.status} to {canonical_new_status}."
            )
        finding.status = canonical_new_status
        session.flush()
        self.audit_service.record(
            session,
            AuditEventType.FINDING_STATUS_UPDATE,
            AuditEventStatus.SUCCESS,
            {
                "finding_id": finding.id,
                "previous_status": previous_status,
                "new_status": canonical_new_status,
            },
            target_id=finding.target_id,
            credential_profile_id=finding.credential_profile_id,
            auth_session_id=finding.session_id,
            scan_job_id=finding.job_id,
        )
        return finding

    def upsert_from_candidate(
        self,
        session: Session,
        *,
        metadata: CheckMetadata,
        candidate: CheckFindingCandidate,
        context: CheckContext,
        check_job_id: int,
    ) -> tuple[Finding, bool]:
        dedup_key = self._build_dedup_key(metadata, candidate, context)
        location_key = self._location_key(candidate.inventory_refs)
        statement = select(Finding).where(
            Finding.target_id == context.target.id,
            Finding.dedup_key == dedup_key,
        )
        finding = session.scalar(statement)
        now = datetime.now(timezone.utc)
        inventory_refs = self._merge_refs(
            [{"kind": "inventory_run", "id": context.inventory_run.id}, *candidate.inventory_refs],
            finding.inventory_refs if finding is not None else [],
        )
        if finding is None:
            finding = Finding(
                title=candidate.title,
                check_name=metadata.name,
                dedup_key=dedup_key,
                location_key=location_key,
                category=metadata.category,
                severity=metadata.severity,
                confidence=metadata.confidence,
                status=FindingStatus.NEW.value,
                target_id=context.target.id,
                credential_profile_id=context.credential_profile.id,
                session_id=context.auth_session.id,
                job_id=check_job_id,
                inventory_refs=inventory_refs,
                evidence_refs=candidate.evidence_refs,
                trigger_explanation=metadata.trigger_explanation,
                false_positive_boundaries=metadata.false_positive_boundaries,
                reproduction_notes=candidate.reproduction_notes,
                remediation_notes=candidate.remediation_notes or metadata.remediation_notes,
                first_seen=now,
                last_seen=now,
            )
            session.add(finding)
            session.flush()
            session.refresh(finding)
            return finding, True

        self._normalize_status(session, finding)
        finding.title = candidate.title
        finding.category = metadata.category
        finding.severity = metadata.severity
        finding.confidence = metadata.confidence
        finding.session_id = context.auth_session.id
        finding.job_id = check_job_id
        finding.location_key = location_key
        finding.inventory_refs = inventory_refs
        finding.trigger_explanation = metadata.trigger_explanation
        finding.false_positive_boundaries = metadata.false_positive_boundaries
        finding.reproduction_notes = candidate.reproduction_notes
        finding.remediation_notes = candidate.remediation_notes or metadata.remediation_notes
        finding.last_seen = now
        if finding.status in {
            FindingStatus.FIXED.value,
            FindingStatus.RETEST_PENDING.value,
            FindingStatus.CLOSED.value,
        }:
            finding.status = FindingStatus.TRIAGED.value
        session.flush()
        return finding, False

    def create_retest_record(
        self,
        session: Session,
        *,
        finding: Finding,
        status: str,
        summary: str,
        inventory_run_id: int | None = None,
        session_id: int | None = None,
        scan_job_id: int | None = None,
    ) -> FindingRetestRun:
        retest = FindingRetestRun(
            finding=finding,
            target_id=finding.target_id,
            credential_profile_id=finding.credential_profile_id,
            session_id=session_id,
            inventory_run_id=inventory_run_id,
            scan_job_id=scan_job_id,
            status=status,
            summary=summary,
        )
        session.add(retest)
        session.flush()
        session.refresh(finding, attribute_names=["retests"])
        session.refresh(retest)
        return retest

    def _build_dedup_key(
        self,
        metadata: CheckMetadata,
        candidate: CheckFindingCandidate,
        context: CheckContext,
    ) -> str:
        role = context.credential_profile.role.strip().lower()
        location_key = self._location_key(candidate.inventory_refs)
        location_scope = (
            sha1(location_key.encode("utf-8")).hexdigest() if location_key else "general"
        )
        return "|".join(
            part
            for part in (
                metadata.name,
                role,
                location_scope,
            )
            if part
        )

    def _location_key(self, refs: list[dict[str, object]]) -> str | None:
        identities = sorted({identity for ref in refs if (identity := self._ref_identity(ref))})
        if not identities:
            return None
        location_key = "|".join(identities)
        if len(location_key) <= 500:
            return location_key
        return f"{location_key[:440]}|sha1:{sha1(location_key.encode('utf-8')).hexdigest()}"

    def _ref_identity(self, ref: dict[str, object]) -> str | None:
        kind = str(ref.get("kind", ""))
        if kind == "endpoint" and ref.get("path"):
            return f"endpoint:{ref['path']}"
        if kind == "page" and ref.get("url"):
            return f"page:{urlparse(str(ref['url'])).path or '/'}"
        if kind == "parameter":
            return f"parameter:{ref.get('source_type', '-')}:{ref.get('name', '-')}"
        if ref.get("subject_ref"):
            return str(ref["subject_ref"])
        return None

    def _merge_refs(
        self,
        incoming: list[dict[str, object]],
        existing: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        merged: list[dict[str, object]] = []
        seen: set[tuple[tuple[str, object], ...]] = set()
        for ref in [*existing, *incoming]:
            normalized = tuple(sorted(ref.items()))
            if normalized in seen:
                continue
            seen.add(normalized)
            merged.append(ref)
        return merged

    def _canonical_status(self, status: str) -> str:
        value = LEGACY_STATUS_MAP.get(status, status)
        try:
            return FindingStatus(value).value
        except ValueError as error:
            allowed = ", ".join(item.value for item in FindingStatus)
            raise InputValidationError(
                f"Unknown finding status '{status}'. Expected one of: {allowed}."
            ) from error

    def _normalize_status(self, session: Session, finding: Finding) -> None:
        canonical = self._canonical_status(finding.status)
        if canonical != finding.status:
            finding.status = canonical
            session.flush()

    def _normalize_statuses(self, session: Session, findings: list[Finding]) -> None:
        for finding in findings:
            self._normalize_status(session, finding)


class CheckRunService:
    """Run plugin-style low-risk checks against authenticated inventory."""

    def __init__(
        self,
        *,
        inventory_service: InventoryBuildService | None = None,
        target_service: TargetService | None = None,
        credential_service: CredentialProfileService | None = None,
        session_service: AuthSessionService | None = None,
        job_service: ScanJobService | None = None,
        finding_service: FindingService | None = None,
        evidence_service: EvidenceService | None = None,
    ) -> None:
        self.inventory_service = inventory_service or InventoryBuildService()
        self.target_service = target_service or TargetService()
        self.credential_service = credential_service or CredentialProfileService()
        self.session_service = session_service or AuthSessionService()
        self.job_service = job_service or ScanJobService()
        self.finding_service = finding_service or FindingService()
        self.evidence_service = evidence_service or EvidenceService()

    def run_inventory_checks(
        self,
        session: Session,
        inventory_run_id: int,
        *,
        selected_check_names: set[str] | None = None,
    ) -> CheckRunSummary:
        inventory_run = self.inventory_service.get(session, inventory_run_id)
        target = self.target_service.get(session, inventory_run.target_id)
        credential = self.credential_service.get(session, inventory_run.credential_profile_id)
        auth_session = self.session_service.get(session, inventory_run.auth_session_id)
        inventory_job = self.job_service.get(session, inventory_run.scan_job_id)
        check_job = self.job_service.create(
            session,
            ScanJobCreate(
                target_id=target.id,
                credential_profile_id=credential.id,
                status=JobStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
                summary=f"Running low-risk checks against inventory {inventory_run.id}.",
            ),
        )

        context = CheckContext(
            inventory_run=inventory_run,
            target=target,
            credential_profile=credential,
            auth_session=auth_session,
            inventory_job=inventory_job,
            pages=list(inventory_run.pages),
            endpoints=list(inventory_run.endpoints),
            parameters=list(inventory_run.parameters),
            annotations=list(inventory_run.annotations),
        )

        created_count = 0
        updated_count = 0
        total_findings = 0
        evidence_count = 0
        plugins = get_registered_checks()
        if selected_check_names is not None:
            plugins = [plugin for plugin in plugins if plugin.metadata.name in selected_check_names]

        for plugin in plugins:
            for candidate in plugin.run(context):
                finding, created = self.finding_service.upsert_from_candidate(
                    session,
                    metadata=plugin.metadata,
                    candidate=candidate,
                    context=context,
                    check_job_id=check_job.id,
                )
                evidence_count += len(
                    self.evidence_service.capture_for_finding(
                        session,
                        finding=finding,
                        context=context,
                        job_id=check_job.id,
                    )
                )
                total_findings += 1
                if created:
                    created_count += 1
                else:
                    updated_count += 1

        check_job.status = JobStatus.COMPLETED.value
        check_job.finished_at = datetime.now(timezone.utc)
        check_job.summary = (
            f"Checks completed for inventory {inventory_run.id}: "
            f"created={created_count}, updated={updated_count}, total={total_findings}, "
            f"evidence={evidence_count}."
        )
        session.flush()

        return CheckRunSummary(
            inventory_run_id=inventory_run.id,
            scan_job_id=check_job.id,
            target_name=target.name,
            profile_name=credential.name,
            session_id=auth_session.id,
            findings_created=created_count,
            findings_updated=updated_count,
            total_findings=total_findings,
            summary=check_job.summary,
        )
