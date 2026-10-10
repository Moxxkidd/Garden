import json

from fastapi.testclient import TestClient

from app.db.bootstrap import session_scope
from app.models.target import Target
from tests.helpers.project_assets import source_run


def test_project_api_create_import_query_and_export(app, seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        target.base_url = "http://localhost:8111/"
        run = source_run(session, target)
        tid, rid = target.id, run.id
    with TestClient(app) as client:
        created = client.post("/api/projects", json={"name": "API project"})
        assert created.status_code == 200, created.text
        pid = created.json()["id"]
        entry = client.post(
            f"/api/projects/{pid}/entries",
            json={"name": "Home", "target_id": tid, "entry_url": "http://localhost:8111/"},
        )
        assert entry.status_code == 200, entry.text
        preview = client.post(
            f"/api/projects/{pid}/imports/preview",
            json={"entry_id": entry.json()["id"], "source_ref": {"source": "scan", "run_id": rid}},
        )
        assert preview.status_code == 200, preview.text
        confirmed = client.post(
            f"/api/projects/{pid}/imports", json={"preview_token": preview.json()["token"]}
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "complete"
        page = client.get(f"/api/projects/{pid}/assets").json()
        assert page["matched"] == 1
        exported = client.get(f"/api/projects/{pid}/export?format=json")
        assert json.loads(exported.text)["exported_count"] == 1
        assert "natural_key" not in exported.text
        assert "source_digest" not in confirmed.text
        rejected = client.post(
            "/api/projects", json={"name": "SECRET"}, headers={"Origin": "https://outside.test"}
        )
        assert rejected.status_code == 400
        assert "SECRET" not in rejected.text


def test_cli_project_create_list_and_import(app, seeded_records):
    from typer.testing import CliRunner

    from app.cli.main import app as cli

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        target.base_url = "http://localhost:8111/"
        run = source_run(session, target)
        tid, rid = target.id, run.id
    runner = CliRunner()
    result = runner.invoke(cli, ["projects", "create", "CLI project"])
    assert result.exit_code == 0, result.output
    pid = json.loads(result.output)["id"]
    result = runner.invoke(
        cli,
        [
            "projects",
            "entry-add",
            str(pid),
            "--target-id",
            str(tid),
            "--url",
            "http://localhost:8111/",
            "--name",
            "Home",
        ],
    )
    assert result.exit_code == 0, result.output
    eid = json.loads(result.output)["id"]
    result = runner.invoke(
        cli,
        [
            "projects",
            "import",
            str(pid),
            "--entry-id",
            str(eid),
            "--source",
            "scan",
            "--run-id",
            str(rid),
        ],
        input="n\n",
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli, ["projects", "assets", str(pid), "--json"])
    assert json.loads(result.output)["matched"] == 0
    result = runner.invoke(
        cli,
        [
            "projects",
            "import",
            str(pid),
            "--entry-id",
            str(eid),
            "--source",
            "scan",
            "--run-id",
            str(rid),
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli, ["projects", "assets", str(pid), "--json"])
    assert json.loads(result.output)["matched"] == 1


def test_quick_api_accepts_project_selection_without_changing_options(app, seeded_records):
    from tests.helpers.project_assets import project_fixture
    from tests.test_scan_preview_pages import HoldingDispatcher

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid, eid, url = project.id, entry.id, entry.entry_url
    with TestClient(app) as client:
        app.state.scan_service.dispatcher = HoldingDispatcher()
        response = client.post("/api/scans", json={"url": url, "project_entry_id": eid})
        assert response.status_code == 202, response.text
        runs = client.get(f"/api/projects/{pid}").json()["runs"]
        assert len(runs) == 1
        assert runs[0]["status"] == "pending"


def test_fixed_identity_api_keeps_project_binding(app):
    from app.schemas.projects import ProjectEntryCreate
    from app.services.projects import ProjectService
    from tests.test_coverage_wizard import _setup_records
    from tests.test_scan_preview_pages import HoldingDispatcher

    setup = _setup_records()
    with session_scope() as session:
        project = ProjectService().create(session, "Fixed identities")
        entry = ProjectService().add_entry(
            session,
            project.id,
            ProjectEntryCreate(target_id=setup.target_id, entry_url=setup.url, name="Entry"),
        )
        pid, eid = project.id, entry.id
    with TestClient(app) as client:
        app.state.scan_service.dispatcher = HoldingDispatcher()
        response = client.post(
            "/api/assessments",
            json={
                "url": setup.url,
                "target_id": setup.target_id,
                "user_profile_id": setup.user_id,
                "admin_profile_id": setup.admin_id,
                "project_entry_id": eid,
            },
        )
        assert response.status_code == 202, response.text
        assert len(client.get(f"/api/projects/{pid}").json()["runs"]) == 1
