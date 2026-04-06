"""Dashboard summary helpers for the minimal web UI."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.credential_profile import CredentialProfile
from app.models.enums import FindingConfidence, FindingSeverity
from app.models.finding import Finding
from app.models.finding_retest import FindingRetestRun
from app.models.scan_job import ScanJob
from app.models.target import Target
from app.services.findings import FindingService


@dataclass
class DashboardSummary:
    total_targets: int
    total_credential_profiles: int
    total_findings: int
    high_severity_findings: int
    high_confidence_findings: int
    recent_jobs: list[ScanJob]
    status_breakdown: dict[str, int]
    category_distribution: list[tuple[str, int]]
    recent_retests: list[FindingRetestRun]


class DashboardService:
    """Build a concise dashboard snapshot for demo and review flows."""

    def __init__(self, *, finding_service: FindingService | None = None) -> None:
        self.finding_service = finding_service or FindingService()

    def build_summary(self, session: Session) -> DashboardSummary:
        total_targets = session.scalar(select(func.count(Target.id))) or 0
        total_credential_profiles = session.scalar(select(func.count(CredentialProfile.id))) or 0
        total_findings = session.scalar(select(func.count(Finding.id))) or 0
        high_severity_findings = (
            session.scalar(
                select(func.count(Finding.id)).where(Finding.severity == FindingSeverity.HIGH.value)
            )
            or 0
        )
        high_confidence_findings = (
            session.scalar(
                select(func.count(Finding.id)).where(
                    Finding.confidence == FindingConfidence.HIGH.value
                )
            )
            or 0
        )
        recent_jobs = list(
            session.scalars(
                select(ScanJob)
                .options(
                    selectinload(ScanJob.target),
                    selectinload(ScanJob.credential_profile),
                )
                .order_by(ScanJob.id.desc())
                .limit(6)
            )
        )
        category_rows = session.execute(
            select(Finding.category, func.count(Finding.id))
            .group_by(Finding.category)
            .order_by(func.count(Finding.id).desc(), Finding.category.asc())
            .limit(8)
        ).all()
        recent_retests = list(
            session.scalars(
                select(FindingRetestRun)
                .options(
                    selectinload(FindingRetestRun.finding),
                    selectinload(FindingRetestRun.target),
                    selectinload(FindingRetestRun.credential_profile),
                )
                .order_by(FindingRetestRun.id.desc())
                .limit(5)
            )
        )
        return DashboardSummary(
            total_targets=total_targets,
            total_credential_profiles=total_credential_profiles,
            total_findings=total_findings,
            high_severity_findings=high_severity_findings,
            high_confidence_findings=high_confidence_findings,
            recent_jobs=recent_jobs,
            status_breakdown=self.finding_service.status_breakdown(session),
            category_distribution=[
                (str(category), int(count)) for category, count in category_rows
            ],
            recent_retests=recent_retests,
        )
