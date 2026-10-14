import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.assessment import AssessmentStartRequest
from app.schemas.collection_queue import CollectionOptions
from app.schemas.scan import ScanOptions
from tests.test_url_scan_pipeline import _service


def test_queue_api_preview_confirm_and_read_only_projection(seeded_records, tmp_path):
    sent = []

    def handle(request):
        sent.append(request.url.path)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/remaining?token=fixture-secret">next</a>',
        )

    scans = _service(tmp_path, handle)
    app = create_app()
    app.state.scan_service = scans
    client = TestClient(app)
    try:
        run = scans.start_assessment(
            AssessmentStartRequest(
                url="http://localhost:8111/",
                target_id=seeded_records["target"].id,
                options=ScanOptions(durable=CollectionOptions(max_requests=1, min_interval_ms=0)),
            )
        )
        response = client.get(f"/api/scans/{run.id}/queue")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        queue = response.json()[0]
        assert not queue["coverage_complete"]
        assert queue["metrics"]["requests_used"] == 1
        qid = queue["id"]
        work = client.get(f"/api/scans/{run.id}/queue/{qid}/work")
        assert work.status_code == 200
        assert "fixture-secret" not in work.text
        exported = client.get(
            f"/api/scans/{run.id}/queue/{qid}/export?format=json&state=suppressed"
        )
        assert exported.status_code == 200
        assert exported.json()["total"] == 0
        assert exported.json()["queue"]["metrics"] == queue["metrics"]
        before = len(sent)
        body = {"queue_id": qid, "options": {"min_interval_ms": 0}}
        preview = client.post(f"/api/scans/{run.id}/resume-preview", json=body)
        assert preview.status_code == 200
        assert len(sent) == before
        confirmation = {"request": body, "token": preview.json()["token"]}
        blocked = client.post(
            f"/api/scans/{run.id}/resume",
            json=confirmation,
            headers={"origin": "https://other.example"},
        )
        assert blocked.status_code == 400
        confirmed = client.post(f"/api/scans/{run.id}/resume", json=confirmation)
        assert confirmed.status_code == 200
        assert len(sent) == before + 1
        duplicate = client.post(f"/api/scans/{run.id}/resume", json=confirmation)
        assert duplicate.json() == confirmed.json()
        assert len(sent) == before + 1
        assert client.get(f"/api/scans/{run.id + 999}/queue/{qid}/work").status_code == 404
        assert client.get("/api/scans/999999/queue").status_code == 404
        assert client.post(f"/api/scans/{run.id}/pause", json={"queue_id": qid}).status_code == 200
    finally:
        client.close()
        scans.shutdown()


def test_project_incremental_api_confirmation_is_bound_and_idempotent(seeded_records, tmp_path):
    from app.db.bootstrap import session_scope
    from app.models.target import Target
    from tests.helpers.project_assets import project_fixture

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid, eid, tid, url = project.id, entry.id, target.id, entry.entry_url
    sent = []

    def handle(request):
        sent.append(request.url.path)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="asset")

    scans = _service(tmp_path, handle)
    app = create_app()
    app.state.scan_service = scans
    client = TestClient(app)
    try:
        scans.start_assessment(
            AssessmentStartRequest(
                url=url,
                target_id=tid,
                project_entry_id=eid,
                options=ScanOptions(durable=CollectionOptions(min_interval_ms=0)),
            )
        )
        native = f"/projects/{pid}/entries/{eid}/incremental"
        page = client.get(native)
        assert page.status_code == 200
        assert "预览增量计划" in page.text
        form_preview = client.post(native + "-preview", data={"stale_after_days": 0})
        assert form_preview.status_code == 200
        assert "确认增量采集" in form_preview.text
        assert len(sent) == 1
        path = f"/api/projects/{pid}/entries/{eid}/incremental"
        body = {
            "project_id": pid,
            "entry_id": eid,
            "stale_after_days": 0,
            "options": {"min_interval_ms": 0},
        }
        preview = client.post(path + "-preview", json=body)
        assert preview.status_code == 200
        assert len(sent) == 1
        confirmation = {"request": body, "token": preview.json()["token"]}
        result = client.post(path, json=confirmation)
        assert result.status_code == 200
        assert len(sent) == 2
        assert client.post(path, json=confirmation).json() == result.json()
        assert len(sent) == 2
        assert client.post(path, json={**confirmation, "token": "changed"}).status_code == 409
    finally:
        client.close()
        scans.shutdown()


def test_pause_during_response_retains_observation_without_reporting_failure(tmp_path):
    sent = []
    app = create_app()
    client = TestClient(app)

    def handle(request):
        sent.append(request.url.path)
        run = scans.list_scans()[0]
        queue = client.get(f"/api/scans/{run.id}/queue").json()[0]
        assert (
            client.post(f"/api/scans/{run.id}/pause", json={"queue_id": queue["id"]}).status_code
            == 200
        )
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text='<a href="/next">next</a>'
        )

    scans = _service(tmp_path, handle)
    app.state.scan_service = scans
    try:
        result = scans.start_scan(
            "http://localhost:8111/", ScanOptions(durable=CollectionOptions(min_interval_ms=0))
        )
        assert len(sent) == 1
        assert result.asset_count == 1, [(f.code, f.message) for f in result.failures]
        assert result.status != "failed"
        queue = client.get(f"/api/scans/{result.id}/queue").json()[0]
        assert queue["status"] == "paused"
        assert not queue["coverage_complete"]
    finally:
        client.close()
        scans.shutdown()
