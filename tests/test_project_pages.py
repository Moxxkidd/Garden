from fastapi.testclient import TestClient

from app.db.bootstrap import session_scope
from app.models.target import Target
from tests.helpers.project_assets import source_run


def test_native_project_import_query_and_annotation(app, seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        target.base_url = "http://localhost:8111/"
        run = source_run(session, target)
        tid, rid = target.id, run.id
    with TestClient(app) as client:
        page = client.get("/projects")
        assert page.status_code == 200
        assert 'method="post"' in page.text
        created = client.post("/projects/create", data={"name": "Web project"})
        assert created.status_code == 200
        path = created.url.path
        assert path.startswith("/projects/")
        added = client.post(
            path + "/entries",
            data={"target_id": tid, "name": "Home", "entry_url": "http://localhost:8111/"},
        )
        assert added.status_code == 200
        pid = int(path.rsplit("/", 1)[1])
        eid = client.get(f"/api/projects/{pid}").json()["entries"][0]["id"]
        preview = client.post(
            path + "/imports/preview", data={"entry_id": eid, "source": "scan", "run_id": rid}
        )
        assert preview.status_code == 200
        assert "确认导入" in preview.text
        import re

        token = re.search(r'name="preview_token"[^>]*value="([^" ]+)"', preview.text).group(1)
        confirmed = client.post(path + "/imports/confirm", data={"preview_token": token})
        assert confirmed.status_code == 200
        catalog = client.get(path + "/assets?q=asset")
        assert "http://localhost:8111/asset" in catalog.text
        asset = client.get(f"/api/projects/{pid}/assets").json()["items"][0]
        detail = client.get(path + "/assets/" + asset["id"])
        assert detail.status_code == 200
        assert "备注" in detail.text


def test_project_entry_scan_preview_retains_membership(app, seeded_records):
    from app.services.project_ingestion import ProjectIngestionService
    from tests.helpers.project_assets import project_fixture
    from tests.test_scan_preview_pages import HoldingDispatcher, Inputs

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid, eid = project.id, entry.id
    with TestClient(app) as client:
        app.state.scan_service.dispatcher = HoldingDispatcher()
        page = client.get(f"/projects/{pid}/entries/{eid}/scan")
        assert page.status_code == 200
        values = Inputs(page.text).values
        preview = client.post("/scans/preview", data=values)
        assert preview.status_code == 200, preview.text
        values = Inputs(preview.text).values
        assert values["project_entry_id"] == str(eid)
        response = client.post("/scans/confirm", data=values, follow_redirects=False)
        assert response.status_code == 303, response.text
        with session_scope() as session:
            assert ProjectIngestionService().runs(session, pid)[0].status == "pending"


def test_identity_page_prefills_project_entry_and_preview(app, seeded_records):
    from tests.helpers.project_assets import project_fixture

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        eid, tid, url = entry.id, target.id, entry.entry_url
    with TestClient(app) as client:
        page = client.get(f"/identities?project_entry_id={eid}")
        assert page.status_code == 200
        assert 'name="project_entry_id"' in page.text
        response = client.post(
            "/identities/preview",
            data={"project_entry_id": eid, "target_id": tid, "url": url, "anonymous": "1"},
        )
        assert response.status_code == 200
        assert f"项目入口 #{eid}" in response.text


def test_native_project_mutation_reports_conflict_status(app, seeded_records):
    from tests.helpers.project_assets import project_fixture

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, _ = project_fixture(session, target)
        pid = project.id
    with TestClient(app) as client:
        response = client.post(f"/projects/{pid}/archive", data={"expected_revision": 0})
        assert response.status_code == 409
        assert "操作未完成" in response.text
        invalid = client.post(f"/projects/{pid}/archive", data={"expected_revision": "invalid"})
        assert invalid.status_code == 400
