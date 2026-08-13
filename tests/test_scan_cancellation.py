"""扫描取消与服务重启后的中断收敛。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.errors import ResourceNotFoundError
from app.db.bootstrap import get_session
from app.main import create_app
from app.models.scan_run import ScanRun
from app.schemas.scan import ScanOptions
from tests.test_assessment_application import build_holding_service


def test_cancel_queued_scan_is_idempotent_and_releases_duplicate_key(tmp_path) -> None:
    service, dispatcher = build_holding_service(tmp_path)
    queued = service.start_scan("http://127.0.0.1:8080/", ScanOptions())

    cancelled = service.cancel_scan(queued.id)
    repeated = service.cancel_scan(queued.id)
    replacement = service.start_scan("http://127.0.0.1:8080/", ScanOptions())

    assert dispatcher.ids == [queued.id, replacement.id]
    assert cancelled.status == "interrupted"
    assert cancelled.current_stage == "interrupted"
    assert cancelled.error_code == "scan_cancelled"
    assert cancelled.report_path is None
    assert repeated.status == cancelled.status
    assert repeated.finished_at.replace(tzinfo=None) == cancelled.finished_at.replace(tzinfo=None)
    assert repeated.error_code == cancelled.error_code
    assert replacement.id != queued.id
    assert {stage.status for stage in cancelled.stages if stage.name == "report"} == {"skipped"}


def test_cancel_missing_scan_raises_not_found(tmp_path) -> None:
    service, _ = build_holding_service(tmp_path)

    try:
        service.cancel_scan(404)
    except ResourceNotFoundError:
        pass
    else:
        raise AssertionError("Expected a missing scan to return a not-found error.")


def test_cancelled_pipeline_does_not_create_a_failure_report(tmp_path) -> None:
    service, _ = build_holding_service(tmp_path)

    def cancel_during_validation(run):
        service.cancel_scan(run.id)
        return None, "Cancelled while validating."

    service.pipeline._validate = cancel_during_validation
    queued = service.start_scan("http://127.0.0.1:8080/", ScanOptions())

    service.execute_scan(queued.id)
    final = service.get_scan(queued.id)

    assert final.status == "interrupted"
    assert final.report_path is None
    assert final.finding_count == 0
    assert not final.failures
    assert next(stage for stage in final.stages if stage.name == "validate").status == "interrupted"


def test_application_startup_interrupts_stale_active_scans() -> None:
    with get_session() as session:
        session.add(
            ScanRun(
                input_url="http://127.0.0.1:8080/",
                normalized_url="http://127.0.0.1:8080/",
                status="running",
                current_stage="collect",
                active_key="stale-active-run",
                options={},
            )
        )
        session.commit()

    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200

    with get_session() as session:
        run = session.query(ScanRun).one()
        assert run.status == "interrupted"
        assert run.current_stage == "interrupted"
        assert run.active_key is None


def test_cancel_api_is_idempotent(app, monkeypatch, tmp_path) -> None:
    service, _ = build_holding_service(tmp_path)
    monkeypatch.setattr("app.main.ScanApplicationService", lambda settings: service)
    with TestClient(app) as client:
        queued = service.start_scan("http://127.0.0.1:8080/", ScanOptions())
        first = client.post(f"/api/scans/{queued.id}/cancel")
        second = client.post(f"/api/scans/{queued.id}/cancel")
        missing = client.post("/api/scans/404/cancel")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "interrupted"
    assert missing.status_code == 404
