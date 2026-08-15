from datetime import datetime, timezone

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanAsset, ScanFinding, ScanRun
from app.services.dashboard import DashboardService


def _add_run(session, *, mode: str, status: str, url: str) -> ScanRun:
    run = ScanRun(
        mode=mode,
        input_url=url,
        normalized_url=url,
        status=status,
        current_stage="finished",
        progress=100,
        options={},
    )
    session.add(run)
    session.flush()
    return run


def _add_asset(session, run: ScanRun, *, suffix: str) -> None:
    session.add(
        ScanAsset(
            scan_run_id=run.id,
            identity_key=f"GET:/{suffix}",
            asset_type="page",
            url=f"{run.normalized_url}{suffix}",
            method="GET",
            attributes={},
            discovered_at=datetime.now(timezone.utc),
        )
    )


def _add_finding(
    session,
    run: ScanRun,
    *,
    key: str,
    category: str,
    severity: str,
    confidence: str,
) -> None:
    session.add(
        ScanFinding(
            scan_run_id=run.id,
            dedup_key=key,
            title=f"Finding {key}",
            category=category,
            severity=severity,
            confidence=confidence,
            summary="Passive finding summary.",
            remediation="Apply the documented remediation.",
            asset_ids=[],
            evidence_ids=[],
            created_at=datetime.now(timezone.utc),
        )
    )


def test_dashboard_summary_aggregates_quick_scan_state_only() -> None:
    service = DashboardService()

    with session_scope() as session:
        first = _add_run(
            session,
            mode="quick",
            status="completed_with_warnings",
            url="http://127.0.0.1:3000/",
        )
        second = _add_run(
            session,
            mode="quick",
            status="failed",
            url="http://127.0.0.1:4000/",
        )
        authenticated = _add_run(
            session,
            mode="authenticated_coverage",
            status="completed",
            url="http://127.0.0.1:5000/",
        )

        _add_asset(session, first, suffix="")
        _add_asset(session, first, suffix="about")
        _add_asset(session, second, suffix="")
        _add_asset(session, authenticated, suffix="admin")

        _add_finding(
            session,
            first,
            key="headers-csp",
            category="security-headers",
            severity="low",
            confidence="high",
        )
        _add_finding(
            session,
            first,
            key="headers-hsts",
            category="security-headers",
            severity="high",
            confidence="medium",
        )
        _add_finding(
            session,
            second,
            key="exposure-debug",
            category="information-exposure",
            severity="high",
            confidence="high",
        )
        _add_finding(
            session,
            authenticated,
            key="admin-only",
            category="authorization",
            severity="high",
            confidence="high",
        )
        session.flush()

        summary = service.build_summary(session)

    assert summary.total_scans == 2
    assert summary.total_assets == 3
    assert summary.total_findings == 3
    assert summary.high_severity_findings == 2
    assert summary.high_confidence_findings == 2
    assert [scan.id for scan in summary.recent_scans] == [second.id, first.id]
    assert summary.status_breakdown == {"completed_with_warnings": 1, "failed": 1}
    assert summary.category_distribution == [
        ("security-headers", 2),
        ("information-exposure", 1),
    ]
