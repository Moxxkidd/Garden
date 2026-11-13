from datetime import datetime, timezone

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.projects import ProjectQuery, ProjectReviewRequest
from app.services.project_catalog import ProjectCatalogService
from app.services.project_curation import ProjectCurationService
from app.services.project_freshness import ProjectFreshnessService
from app.services.projects import ProjectService
from tests.helpers.project_assets import project_fixture, source_run
from tests.test_project_comparison import ingest


def test_age_and_partial_recheck_preserve_human_choice(seeded_records):
    def clock():
        return datetime(2026, 10, 12, tzinfo=timezone.utc)

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        ingest(session, project, entry, source_run(session, target, day=1))
        row = ProjectCatalogService().list(session, project.id, ProjectQuery()).items[0]
        curation = ProjectCurationService()
        curation.review(
            session,
            project.id,
            ProjectReviewRequest(
                asset_ids=[row["id"]],
                state="keep",
                reason="人工整理",
                expected_revision=project.revision,
            ),
        )
        # Another low-budget/partial run does not observe this subject.
        ingest(
            session,
            project,
            entry,
            source_run(session, target, day=2, path="/other", completeness="incomplete"),
        )
        freshness = ProjectFreshnessService(clock=clock)
        result = freshness.view(session, project.id)
        assert result["freshness_days"] == 7
        assert result["older_assets"] == 2
        assert result["comparisons"][0]["counts"]["unknown"] == 2
        assert "deleted" not in result["comparisons"][0]["counts"]
        review = ProjectCatalogService().reviews(session, project.id, [row["id"]])[row["id"]]
        assert review["state"] == "keep" and not review["needs_review"]
        freshness.configure(session, project.id, 14, project.revision)
        assert freshness.view(session, project.id)["older_assets"] == 0
        assert ProjectService().get(session, project.id).freshness_days == 14
        items = ProjectCatalogService(clock=clock).list(session, project.id, ProjectQuery()).items
        assert all(r["freshness"] == "recent" for r in items)
        assert result["latest_sources"][0]["scope"]["completeness"] == "incomplete"
        assert "budget" in result["latest_sources"][0]
