"""单 URL 扫描 Dashboard 汇总。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import AssessmentMode, FindingConfidence, FindingSeverity
from app.models.scan_run import ScanAsset, ScanFinding, ScanRun


@dataclass
class DashboardSummary:
    total_scans: int
    total_assets: int
    total_findings: int
    high_severity_findings: int
    high_confidence_findings: int
    recent_scans: list[ScanRun]
    status_breakdown: dict[str, int]
    category_distribution: list[tuple[str, int]]


class DashboardService:
    """汇总低门槛 quick scan 的用户可见结果。"""

    def build_summary(self, session: Session) -> DashboardSummary:
        quick_mode = AssessmentMode.QUICK.value
        total_scans = (
            session.scalar(select(func.count(ScanRun.id)).where(ScanRun.mode == quick_mode)) or 0
        )
        total_assets = (
            session.scalar(
                select(func.count(ScanAsset.id))
                .join(ScanRun, ScanAsset.scan_run_id == ScanRun.id)
                .where(ScanRun.mode == quick_mode)
            )
            or 0
        )
        total_findings = (
            session.scalar(
                select(func.count(ScanFinding.id))
                .join(ScanRun, ScanFinding.scan_run_id == ScanRun.id)
                .where(ScanRun.mode == quick_mode)
            )
            or 0
        )
        high_severity_findings = (
            session.scalar(
                select(func.count(ScanFinding.id))
                .join(ScanRun, ScanFinding.scan_run_id == ScanRun.id)
                .where(
                    ScanRun.mode == quick_mode,
                    ScanFinding.severity == FindingSeverity.HIGH.value,
                )
            )
            or 0
        )
        high_confidence_findings = (
            session.scalar(
                select(func.count(ScanFinding.id))
                .join(ScanRun, ScanFinding.scan_run_id == ScanRun.id)
                .where(
                    ScanRun.mode == quick_mode,
                    ScanFinding.confidence == FindingConfidence.HIGH.value,
                )
            )
            or 0
        )
        recent_scans = list(
            session.scalars(
                select(ScanRun)
                .where(ScanRun.mode == quick_mode)
                .order_by(ScanRun.id.desc())
                .limit(6)
            )
        )
        status_rows = session.execute(
            select(ScanRun.status, func.count(ScanRun.id))
            .where(ScanRun.mode == quick_mode)
            .group_by(ScanRun.status)
            .order_by(ScanRun.status.asc())
        ).all()
        category_rows = session.execute(
            select(ScanFinding.category, func.count(ScanFinding.id))
            .join(ScanRun, ScanFinding.scan_run_id == ScanRun.id)
            .where(ScanRun.mode == quick_mode)
            .group_by(ScanFinding.category)
            .order_by(func.count(ScanFinding.id).desc(), ScanFinding.category.asc())
            .limit(8)
        ).all()
        return DashboardSummary(
            total_scans=total_scans,
            total_assets=total_assets,
            total_findings=total_findings,
            high_severity_findings=high_severity_findings,
            high_confidence_findings=high_confidence_findings,
            recent_scans=recent_scans,
            status_breakdown={str(status): int(count) for status, count in status_rows},
            category_distribution=[
                (str(category), int(count)) for category, count in category_rows
            ],
        )
