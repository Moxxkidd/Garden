"""One selected project view has the same report facts across public adapters."""

import json

from fastapi.testclient import TestClient

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.projects import ProjectQuery, ProjectReviewRequest
from app.services.asset_delivery import AssetDeliveryService
from app.services.project_curation import ProjectCurationService
from tests.helpers.project_assets import project_fixture, source_run
from tests.test_project_comparison import ingest


def test_selected_report_keeps_subjects_identities_review_and_evidence(app, seeded_records):
    service = AssetDeliveryService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        for day, profile in ((1, None), (2, seeded_records["credential"].id)):
            run = source_run(
                session, target, day=day, profile_id=profile, completeness="incomplete"
            )
            run.options = {"max_browser_requests": 20}
            ingest(session, project, entry, run)
        pid = project.id
        selected = service.page(session, pid, ProjectQuery(q="asset"))
        aid = selected["items"][0]["id"]
        ProjectCurationService().review(
            session,
            pid,
            ProjectReviewRequest(
                asset_ids=[aid],
                state="keep",
                reason="人工留存，并非业务有效证明",
                expected_revision=selected["revision"],
            ),
        )
    with session_scope() as session:
        document = service.page(session, pid, ProjectQuery(q="asset"))
        report = document["report"]
        assert report["summary"]["asset_count"] == 1
        assert report["summary"]["observation_count"] == 2
        assert len(report["summary"]["identities"]) == 2
        assert report["summary"]["review"]["keep"] == 1
        assert report["summary"]["coverage"]["incomplete_sources"] == 2
        assert report["evidence"]["content"] == "public_references_only"
        assert report["evidence"]["sources"][0]["budget"]["max_browser_requests"] == 20
        assert document["items"][0]["review"]["state"] == "keep"
        markdown = service.export(session, pid, ProjectQuery(q="asset"), "markdown")
        assert "1 个主体，2 条观察" in markdown and "人工保留 1" in markdown
    client = TestClient(app)
    response = client.get(f"/api/projects/{pid}/delivery", params={"q": "asset"})
    assert response.json()["report"] == report
    web = client.get(f"/projects/{pid}/delivery?q=asset")
    assert web.status_code == 200 and "1 个主体，2 条观察" in web.text
    assert "业务有效" in web.text
    assert "max_browser_requests" in json.dumps(report)
