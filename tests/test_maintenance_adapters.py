"""One HTTP/native-form journey over shared maintenance services."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.db.bootstrap import session_scope
from app.models.target import Target
from tests.helpers.project_assets import project_fixture, source_run
from tests.test_project_comparison import ingest


def test_maintenance_preview_archive_restore_and_storage_forms(app, seeded_records, tmp_path):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        ingest(session, project, entry, source_run(session, target))
        pid, eid = project.id, entry.id
    with TestClient(app) as client:
        assert client.get(f"/api/projects/{pid}/freshness").json()["freshness_days"] == 7
        assert client.get(f"/api/projects/{pid}/schedules").json() == []
        assert client.get(f"/projects/{pid}/maintenance").status_code == 200
        p = client.post(
            f"/api/projects/{pid}/schedules/preview",
            json={
                "request": {
                    "entry_id": eid,
                    "start_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                }
            },
        )
        assert p.status_code == 200
        row = client.post(
            f"/api/projects/{pid}/schedules", json={"token": p.json()["token"], "enable": True}
        ).json()
        assert row["enabled"]
        rev = client.get(f"/api/projects/{pid}").json()["project"]["revision"]
        assert (
            client.post(
                f"/projects/{pid}/visibility", data={"expected_revision": rev, "action": "archive"}
            ).status_code
            == 200
        )
        assert client.get("/api/projects").json() == []
        assert len(client.get("/api/projects?include_archived=true").json()) == 1
        assert not client.get(f"/api/projects/{pid}/schedules").json()[0]["enabled"]
        rev = client.get(f"/api/projects/{pid}").json()["project"]["revision"]
        assert (
            client.post(
                f"/projects/{pid}/visibility", data={"expected_revision": rev, "action": "restore"}
            ).status_code
            == 200
        )
        assert not client.get(f"/api/projects/{pid}/schedules").json()[0]["enabled"]
        assert client.get("/storage").status_code == 200
        preview = client.post("/storage/cleanup-preview", data={"retention_days": 30})
        assert preview.status_code == 200 and "确认清理范围" in preview.text
        assert (
            client.post(
                "/api/storage/backups",
                json={"destination": str(tmp_path / "denied")},
                headers={"Origin": "https://foreign.invalid"},
            ).status_code
            == 400
        )
