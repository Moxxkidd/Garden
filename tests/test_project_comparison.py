import pytest

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.services.project_comparison import ProjectComparisonService
from app.services.project_ingestion import ProjectIngestionService
from tests.helpers.project_assets import import_request, project_fixture, source_run


def ingest(session, project, entry, run):
    service = ProjectIngestionService()
    preview = service.preview(session, project.id, import_request(entry, run))
    return service.confirm(session, project.id, preview.token)


def test_response_change_is_not_disappearance(seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        first = ingest(session, project, entry, source_run(session, target, day=1))
        second = ingest(
            session, project, entry, source_run(session, target, day=2, status_code=404)
        )
        result = ProjectComparisonService().compare(session, project.id, first.id, second.id)
        assert result.counts["attributes_changed"] == 1
        assert result.items[0]["before"][0]["record"]["status_codes"] == [200]
        assert result.items[0]["after"][0]["record"]["status_codes"] == [404]
        assert "deleted" not in result.counts


@pytest.mark.parametrize("incomplete,expected", [(False, "not_observed"), (True, "unknown")])
def test_missing_requires_complete_comparable_coverage(seeded_records, incomplete, expected):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        first = ingest(session, project, entry, source_run(session, target, day=1))
        second = ingest(
            session,
            project,
            entry,
            source_run(
                session,
                target,
                day=2,
                path="/other",
                completeness="incomplete" if incomplete else "complete",
            ),
        )
        result = ProjectComparisonService().compare(session, project.id, first.id, second.id)
        previous = next(item for item in result.items if item["before"])
        assert previous["status"] == expected
        if incomplete:
            assert previous["reasons"]


@pytest.mark.parametrize("health", [None, "expired", "ready"])
def test_authenticated_comparison_requires_positive_health(seeded_records, health):
    from sqlalchemy import select

    from app.models.scan_context import ScanContext

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        profile_id = seeded_records["credential"].id
        runs = []
        for day in (1, 2):
            source = source_run(session, target, day=day, profile_id=profile_id)
            context = session.scalar(
                select(ScanContext).where(ScanContext.scan_run_id == source.id)
            )
            context.health_status = health
            session.flush()
            runs.append(ingest(session, project, entry, source))
        result = ProjectComparisonService().compare(session, project.id, runs[0].id, runs[1].id)
        assert result.items[0]["status"] == ("unchanged" if health == "ready" else "unknown")
        if health != "ready":
            assert "identity_coverage_unknown" in result.items[0]["reasons"]


@pytest.mark.parametrize(
    "change,reason",
    [
        ("configuration", "configuration_changed"),
        ("recovery", "partial_recovery"),
        ("time", "source_time_unknown"),
        ("mode", "mode_not_comparable"),
    ],
)
def test_incompatible_source_cannot_claim_missing_asset(seeded_records, change, reason):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        first = ingest(session, project, entry, source_run(session, target, day=1))
        source = source_run(session, target, day=2, path="/other")
        if change == "configuration":
            source.options = {"max_pages": 1}
        elif change == "recovery":
            source.parent_run_id = first.run_id
        elif change == "time":
            source.finished_at = None
        else:
            source.mode = "identity_collection"
        session.flush()
        second = ingest(session, project, entry, source)
        result = ProjectComparisonService().compare(session, project.id, first.id, second.id)
        assert all(item["status"] == "unknown" for item in result.items)
        assert reason in result.limitations
