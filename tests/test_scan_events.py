"""Real streaming connection, disconnect and snapshot recovery without target requests."""

import json

import httpx

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanRun
from tests.helpers.project_assets import source_run
from tests.test_scan_preview_e2e import _serve


def snapshot(lines):
    for line in lines:
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError("stream ended without snapshot")


def test_events_reconnect_gets_current_redacted_snapshot(app, seeded_records):
    with _serve(app) as origin:
        with session_scope() as session:
            run = source_run(session, seeded_records["target"])
            run.status = "running"
            run.progress = 8
            run.options = {**run.options, "feedback_version": 1}
            run.error_message = "PRIVATE_SECRET"
            run_id = run.id
        with httpx.Client(trust_env=False, timeout=3) as client:
            with client.stream("GET", f"{origin}/api/scans/{run_id}/events") as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                first = snapshot(response.iter_lines())
                assert first["progress"] == 8
                assert "PRIVATE_SECRET" not in json.dumps(first)
            with session_scope() as session:
                row = session.get(ScanRun, run_id)
                row.progress = 50
            with client.stream("GET", f"{origin}/api/scans/{run_id}/events") as response:
                assert snapshot(response.iter_lines())["progress"] == 50
            with session_scope() as session:
                row = session.get(ScanRun, run_id)
                row.status = "completed"
                row.progress = 100
            with client.stream("GET", f"{origin}/api/scans/{run_id}/events") as response:
                assert snapshot(response.iter_lines())["status"] == "completed"
            assert client.get(f"{origin}/api/scans/999999/events").status_code == 404
