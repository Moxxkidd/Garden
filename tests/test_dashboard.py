from app.db.bootstrap import session_scope
from app.services.dashboard import DashboardService


def test_dashboard_summary_aggregates_workflow_state(seeded_findings) -> None:
    service = DashboardService()

    with session_scope() as session:
        summary = service.build_summary(session)

    assert summary.total_targets >= 1
    assert summary.total_credential_profiles >= 1
    assert summary.total_findings >= 1
    assert summary.recent_jobs
    assert "new" in summary.status_breakdown
    assert summary.category_distribution
