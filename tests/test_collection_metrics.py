"""Durable metrics are read back through the public queue projection."""

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.collection_queue import CollectionOptions
from app.services.collection_queue import CollectionQueueService
from app.services.collection_storage import CollectionStorage
from tests.helpers.project_assets import source_run


def test_project_metrics_and_final_report_freeze_after_automatic_ingestion(
    seeded_records, tmp_path
):
    from pathlib import Path

    import httpx

    from app.schemas.assessment import AssessmentStartRequest
    from app.schemas.projects import ProjectSourceRef
    from app.schemas.scan import ScanOptions
    from app.services.project_lifecycle import ProjectLifecycleService
    from tests.helpers.project_assets import project_fixture
    from tests.test_url_scan_pipeline import _service

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid, eid, tid, url = project.id, entry.id, target.id, entry.entry_url
    service = _service(tmp_path, lambda request: httpx.Response(200, text="fixture"))
    try:
        request = AssessmentStartRequest(
            url=url,
            target_id=tid,
            project_entry_id=eid,
            options=ScanOptions(durable=CollectionOptions(min_interval_ms=0)),
        )
        first = service.start_assessment(request)
        report = Path(first.report_path).read_bytes()
        assert "新增项目主体：1；新增项目观察：1" in report.decode()
        second = service.start_assessment(request)
        with session_scope() as session:
            a = CollectionQueueService().for_run(session, first.id)[0]["metrics"]
            b = CollectionQueueService().for_run(session, second.id)[0]["metrics"]
            assert a["project_ingestion"] == {
                "state": "complete",
                "projects": [
                    {
                        "project_id": pid,
                        "status": "complete",
                        "new_subjects": 1,
                        "new_observations": 1,
                    }
                ],
            }
            assert b["project_ingestion"]["projects"][0]["new_subjects"] == 0
            assert b["project_ingestion"]["projects"][0]["new_observations"] == 1
        ProjectLifecycleService().reconcile(ProjectSourceRef(source="scan", run_id=first.id))
        assert Path(first.report_path).read_bytes() == report
    finally:
        service.shutdown()


def test_duplicate_submissions_are_counted_once_per_repeated_candidate(seeded_records, tmp_path):
    queues = CollectionQueueService(storage=CollectionStorage(tmp_path / "private"))
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        queue = queues.create(session, run.id, run.contexts[0].id, CollectionOptions())
        qid = queue["id"]
        assert queue["metrics"]["duplicate_work_submissions"] == 0
        first = queues.enqueue(session, qid, target.base_url + "/asset?key=fixture-a")
        assert (
            queues.enqueue(session, qid, target.base_url + "/asset?key=fixture-a")["id"]
            == first["id"]
        )
        queues.enqueue(session, qid, target.base_url + "/asset?key=fixture-a", source="html")
        queues.enqueue(session, qid, target.base_url + "/asset?key=fixture-b")
        queues.enqueue(session, qid, target.base_url + "/asset?key=fixture-a", method="HEAD")
    with session_scope() as session:
        result = queues.get(session, qid)
        assert result["counts"] == {"pending": 3}
        assert result["metrics"]["duplicate_work_submissions"] == 2
        assert result["metrics"]["requests_used"] == 0


def test_failed_project_index_is_unknown_and_retry_does_not_rewrite_scan_history(
    seeded_records, tmp_path
):
    from pathlib import Path

    import httpx
    from sqlalchemy import text

    from app.schemas.assessment import AssessmentStartRequest
    from app.schemas.projects import ProjectSourceRef
    from app.schemas.scan import ScanOptions
    from app.services.project_ingestion import ProjectIngestionService
    from app.services.project_lifecycle import ProjectLifecycleService
    from tests.helpers.project_assets import project_fixture
    from tests.test_url_scan_pipeline import _service

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid, eid, tid, url = project.id, entry.id, target.id, entry.entry_url
        session.execute(
            text(
                "CREATE TRIGGER reject_project_observation BEFORE INSERT ON project_observations "
                "BEGIN SELECT RAISE(ABORT, 'fixture project storage failure'); END"
            )
        )
    service = _service(tmp_path, lambda request: httpx.Response(200, text="fixture"))
    try:
        run = service.start_assessment(
            AssessmentStartRequest(
                url=url,
                target_id=tid,
                project_entry_id=eid,
                options=ScanOptions(durable=CollectionOptions(min_interval_ms=0)),
            )
        )
        assert run.status == "completed"
        report = Path(run.report_path).read_bytes()
        assert "新增项目主体：未记录；新增项目观察：未记录" in report.decode()
        with session_scope() as session:
            metrics = CollectionQueueService().for_run(session, run.id)[0]["metrics"]
            assert metrics["project_ingestion"] == {
                "state": "unknown",
                "projects": [
                    {
                        "project_id": pid,
                        "status": "failed",
                        "new_subjects": None,
                        "new_observations": None,
                    }
                ],
            }
            membership = ProjectIngestionService().runs(session, pid)[0]
            mid = membership.id
            session.execute(text("DROP TRIGGER reject_project_observation"))
        ProjectLifecycleService().retry(mid)
        ProjectLifecycleService().reconcile(ProjectSourceRef(source="scan", run_id=run.id))
        with session_scope() as session:
            assert ProjectIngestionService().runs(session, pid)[0].status == "complete"
            assert CollectionQueueService().for_run(session, run.id)[0]["metrics"] == metrics
        assert Path(run.report_path).read_bytes() == report
    finally:
        service.shutdown()


def test_old_queue_upgrade_does_not_invent_duplicate_counts(seeded_records, tmp_path):
    from sqlalchemy import text

    from app.core.settings import get_settings
    from app.db.bootstrap import upgrade_database
    from tests.test_migrations import downgrade_database

    queues = CollectionQueueService(storage=CollectionStorage(tmp_path / "private"))
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = queues.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
        url = target.base_url + "/legacy"
        queues.enqueue(session, qid, url)
        # Construct a pre-telemetry fixture, then perform the actual schema round trip.
        session.execute(
            text("UPDATE collection_queues SET telemetry = NULL WHERE id=:id"), {"id": qid}
        )
    downgrade_database(get_settings().database_url, "0010")
    upgrade_database(get_settings().database_url)
    with session_scope() as session:
        old_metrics = queues.get(session, qid)["metrics"]
        assert old_metrics["duplicate_work_submissions"] is None
        assert old_metrics["active_seconds"] is None
        assert old_metrics["confirmed_observations_per_request"] is None
        queues.enqueue(session, qid, url)
        assert queues.get(session, qid)["metrics"]["duplicate_work_submissions"] is None
        assert queues.get(session, qid)["counts"] == {"pending": 1}


def test_metrics_history_cannot_be_lost_by_downgrade(seeded_records, tmp_path):
    import pytest

    from app.core.settings import get_settings
    from tests.test_migrations import downgrade_database

    queues = CollectionQueueService(storage=CollectionStorage(tmp_path / "private"))
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = queues.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
        queues.enqueue(session, qid, target.base_url)
        queues.enqueue(session, qid, target.base_url)
    with pytest.raises(RuntimeError, match="collection history metrics"):
        downgrade_database(get_settings().database_url, "0010")
    with session_scope() as session:
        assert queues.get(session, qid)["metrics"]["duplicate_work_submissions"] == 1


def test_http_observation_efficiency_is_shared_with_report_and_web(tmp_path):
    import httpx
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.schemas.scan import ScanOptions
    from tests.test_url_scan_pipeline import _service

    service = _service(
        tmp_path,
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"}, text="<p>observed</p>"
        ),
    )
    try:
        run = service.start_scan(
            "http://localhost:8111/", ScanOptions(durable=CollectionOptions(min_interval_ms=0))
        )
        with session_scope() as session:
            result = CollectionQueueService().for_run(session, run.id)[0]
            assert result["metrics"]["confirmed_observations"] == 1
            assert result["metrics"]["uncertain_observations"] == 0
            assert result["metrics"]["confirmed_observations_per_request"] == 1.0
            assert result["metrics"]["transport"] == "http"
            assert result["metrics"]["project_ingestion"] == {
                "state": "not_applicable",
                "projects": [],
            }
        with TestClient(create_app()) as client:
            api = client.get(f"/api/scans/{run.id}/queue").json()[0]
            assert api["metrics"] == result["metrics"]
            page = client.get(f"/scans/{run.id}/queue").text
            assert "已确认观察：1" in page
            assert "本轮已确认观察 / 发送尝试：1.000" in page
        from pathlib import Path

        report = Path(run.report_path).read_text()
        assert "已确认观察：1" in report
        assert "本轮已确认观察 / 发送尝试：1.000" in report
    finally:
        service.shutdown()


def test_identity_observation_counts_exclude_other_contexts_and_health_cost_is_visible(
    seeded_records, tmp_path
):
    from datetime import datetime, timezone

    from app.models.scan_run import ScanEvidence
    from app.services.collection_admission import CollectionAdmissionService

    queues = CollectionQueueService(storage=CollectionStorage(tmp_path / "private"))
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target, profile_id=seeded_records["credential"].id)
        run.mode = "identity_collection"
        asset = run.assets[0]
        for assessment in ["confirmed", "identity_uncertain", "auth_diagnostic", None]:
            session.add(
                ScanEvidence(
                    scan_run_id=run.id,
                    asset_id=asset.id,
                    evidence_type="http_response",
                    title="fixture",
                    source_url=asset.url,
                    summary="fixture",
                    data={"identity_assessment": assessment},
                    collected_at=datetime.now(timezone.utc),
                )
            )
        other = source_run(session, target)
        session.add(
            ScanEvidence(
                scan_run_id=other.id,
                asset_id=other.assets[0].id,
                evidence_type="http_response",
                title="other",
                source_url=asset.url,
                summary="fixture",
                data={},
                collected_at=datetime.now(timezone.utc),
            )
        )
        qid = queues.create(
            session, run.id, run.contexts[0].id, CollectionOptions(min_interval_ms=0)
        )["id"]
        assert queues.get(session, qid)["metrics"]["confirmed_observations_per_request"] is None
        token = queues.lease(session, qid)
        admission = CollectionAdmissionService(queues=queues)
        for kind in ["browser", "identity_health"]:
            attempt = admission.reserve(session, qid, token, target.base_url, kind=kind)
            admission.admit(session, attempt["id"], token)
            admission.finish(session, attempt["id"], token, status_code=200)
        metrics = queues.get(session, qid)["metrics"]
        assert metrics["confirmed_observations"] == 1
        assert metrics["uncertain_observations"] == 1
        assert metrics["diagnostic_observations"] == 1
        assert metrics["unknown_identity_observations"] == 1
        assert metrics["confirmed_observations_per_request"] == 0.5
        assert metrics["verification_requests"] == 1
        assert metrics["transport"] == "browser"


def test_queue_timing_separates_active_wait_and_cooldown_and_stays_frozen(seeded_records, tmp_path):
    now = [100.0]
    queues = CollectionQueueService(
        storage=CollectionStorage(tmp_path / "private"), clock=lambda: now[0]
    )
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = queues.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
        now[0] = 103.0
        token = queues.lease(session, qid)
        now[0] = 105.0
        queues.set_waiting(session, qid, token, "cooldown")
        now[0] = 110.0
        queues.heartbeat(session, qid, token)
        now[0] = 112.0
        queues.set_waiting(session, qid, token, None)
        now[0] = 115.0
        queues.close(session, qid, token)
    now[0] = 130.0
    with session_scope() as session:
        metrics = queues.get(session, qid)["metrics"]
        assert metrics["queued_seconds"] == 3.0
        assert metrics["active_seconds"] == 5.0
        assert metrics["wait_seconds"] == 7.0
        assert metrics["cooldown_seconds"] == 7.0
        assert metrics["timing_complete"] is True


def test_http_retry_cost_and_actual_cooldown_are_counted(tmp_path):
    import httpx

    from app.schemas.scan import ScanOptions
    from tests.test_url_scan_pipeline import _service

    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(
            429 if len(calls) == 1 else 200,
            headers={"content-type": "text/html", "retry-after": "1"},
            text="<p>fixture</p>",
        )

    service = _service(tmp_path, handle)
    try:
        run = service.start_scan(
            "http://localhost:8111/",
            ScanOptions(
                durable=CollectionOptions(max_requests=2, min_interval_ms=0, retry_attempts=1)
            ),
        )
        assert calls == ["/", "/"]
        with session_scope() as session:
            metrics = CollectionQueueService().for_run(session, run.id)[0]["metrics"]
            assert metrics["requests_used"] == 2
            assert metrics["retry_requests"] == 1
            assert metrics["confirmed_observations"] == 1
            assert metrics["confirmed_observations_per_request"] == 0.5
            assert metrics["cooldown_seconds"] >= 0.8
            assert metrics["wait_seconds"] >= metrics["cooldown_seconds"]
            assert metrics["active_seconds"] >= 0
            assert metrics["timing_complete"]
    finally:
        service.shutdown()


def test_lost_worker_only_retains_measured_timing_samples(seeded_records, tmp_path):
    now = [100.0]
    queues = CollectionQueueService(
        storage=CollectionStorage(tmp_path / "private"), clock=lambda: now[0]
    )
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = queues.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
        token = queues.lease(session, qid)
        now[0] = 105.0
        queues.heartbeat(session, qid, token)
    now[0] = 300.0
    with session_scope() as session:
        assert queues.recover_expired(session) == 1
        metrics = queues.get(session, qid)["metrics"]
        assert metrics["active_seconds"] == 5.0
        assert metrics["timing_complete"] is False
