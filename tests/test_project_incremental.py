from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.project_incremental import ProjectIncrementalRequest
from app.schemas.projects import ProjectSourceRef
from app.services.project_incremental import ProjectIncrementalService
from app.services.project_lifecycle import ProjectLifecycleService
from tests.helpers.project_assets import project_fixture, source_run


def test_incremental_preview_explains_legacy_observations_without_original_material(seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target)
        ref = ProjectSourceRef(source="scan", run_id=run.id)
        ProjectLifecycleService().attach(session, entry.id, ref)
        project_id, entry_id = project.id, entry.id
    ProjectLifecycleService().reconcile(ref)
    with session_scope() as session:
        result = ProjectIncrementalService().preview(
            session,
            ProjectIncrementalRequest(
                project_id=project_id,
                entry_id=entry_id,
            ),
        )
        assert result["work_count"] == 0
        assert result["blocked_count"] == 1
        assert result["blocked"][0]["reason"] == "original_request_unavailable"
        assert not session.new and not session.dirty


def test_incremental_plan_prioritizes_recovery_new_and_stale_proven_work(seeded_records, tmp_path):
    import httpx

    from app.schemas.assessment import AssessmentStartRequest
    from app.schemas.collection_queue import CollectionOptions
    from app.schemas.scan import ScanOptions
    from tests.test_url_scan_pipeline import _service

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid, eid, tid, url = project.id, entry.id, target.id, entry.entry_url
    sent = []

    def handle(request):
        sent.append(request.url.path)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/one">one</a><a href="/two">two</a>',
        )

    scans = _service(tmp_path, handle)
    try:
        scans.start_assessment(
            AssessmentStartRequest(
                url=url,
                target_id=tid,
                project_entry_id=eid,
                options=ScanOptions(durable=CollectionOptions(max_requests=1, min_interval_ms=0)),
            )
        )
        planner = ProjectIncrementalService(scans=scans)
        request = ProjectIncrementalRequest(
            project_id=pid,
            entry_id=eid,
            stale_after_days=0,
            options=CollectionOptions(min_interval_ms=0),
        )
        with session_scope() as session:
            preview = planner.preview(session, request)
            assert preview["work_count"] == 3
            assert [w["reason"] for w in preview["work"]] == [
                "recovery",
                "new_candidate",
                "stale_observation",
            ]
            assert preview["blocked_count"] == 0
            root_id = preview["work"][-1]["asset_ids"][0]
            important = planner.preview(
                session, request.model_copy(update={"important_asset_ids": [root_id]})
            )
            assert important["work"][0]["reason"] == "important"
            assert important["scan_options"]["max_pages"] == 50
            assert important["scope_note"]

        from datetime import datetime, timedelta, timezone

        aged = ProjectIncrementalService(
            scans=scans, clock=lambda: datetime.now(timezone.utc) + timedelta(days=22)
        )
        with session_scope() as session:
            aged_preview = aged.preview(session, request)
            assert [w["reason"] for w in aged_preview["work"]] == [
                "stale_observation",
                "recovery",
                "new_candidate",
            ]
            assert aged_preview["work"][0]["aged"]
        sent.clear()
        with session_scope() as session:
            child_id = planner.start(session, request, preview["token"])
        assert sent == ["/one", "/two", "/"]
        assert scans.get_scan(child_id).asset_count == 3
        with session_scope() as session:
            assert planner.start(session, request, preview["token"]) == child_id
        assert len(sent) == 3
        with session_scope() as session:
            from app.services.project_ingestion import ProjectIngestionService

            assert len(ProjectIngestionService().runs(session, pid)) == 2
    finally:
        scans.shutdown()


def test_incremental_confirmation_rejects_changed_target_in_cached_session(
    seeded_records, tmp_path
):
    import httpx
    import pytest

    from app.core.errors import ConflictError
    from app.schemas.assessment import AssessmentStartRequest
    from app.schemas.collection_queue import CollectionOptions
    from app.schemas.scan import ScanOptions
    from tests.test_url_scan_pipeline import _service

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid, eid, tid, url = project.id, entry.id, target.id, entry.entry_url
    sent = []

    def handle(request):
        sent.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text="ok")

    scans = _service(tmp_path, handle)
    try:
        scans.start_assessment(
            AssessmentStartRequest(
                url=url,
                target_id=tid,
                project_entry_id=eid,
                options=ScanOptions(durable=CollectionOptions(min_interval_ms=0)),
            )
        )
        planner = ProjectIncrementalService(scans=scans)
        request = ProjectIncrementalRequest(project_id=pid, entry_id=eid, stale_after_days=0)
        with session_scope() as session:
            preview = planner.preview(session, request)
            with session_scope() as other:
                other.get(Target, tid).type = "changed-after-preview"
            before = len(sent)
            with pytest.raises(ConflictError):
                planner.start(session, request, preview["token"])
            assert len(sent) == before
    finally:
        scans.shutdown()


def test_simultaneous_incremental_confirmations_create_one_run(seeded_records, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import httpx

    from app.schemas.assessment import AssessmentStartRequest
    from app.schemas.collection_queue import CollectionOptions
    from app.schemas.scan import ScanOptions
    from tests.test_url_scan_pipeline import _service

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid, eid, tid, url = project.id, entry.id, target.id, entry.entry_url
    sent = []

    def handle(request):
        sent.append(request.url.path)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="asset")

    scans = _service(tmp_path, handle)
    try:
        scans.start_assessment(
            AssessmentStartRequest(
                url=url,
                target_id=tid,
                project_entry_id=eid,
                options=ScanOptions(durable=CollectionOptions(min_interval_ms=0)),
            )
        )
        planner = ProjectIncrementalService(scans=scans)
        request = ProjectIncrementalRequest(
            project_id=pid,
            entry_id=eid,
            stale_after_days=0,
            options=CollectionOptions(min_interval_ms=0),
        )
        with session_scope() as session:
            preview = planner.preview(session, request)
        barrier = Barrier(2)

        def confirm():
            barrier.wait(timeout=5)
            with session_scope() as session:
                return planner.start(session, request, preview["token"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(confirm) for _ in range(2)]
            ids = [future.result(timeout=15) for future in futures]
        assert len(set(ids)) == 1
        assert len(sent) == 2
    finally:
        scans.shutdown()


def test_incremental_preview_rejects_unknown_important_assets(seeded_records):
    import pytest

    from app.core.errors import InputValidationError

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        request = ProjectIncrementalRequest(
            project_id=project.id,
            entry_id=entry.id,
            important_asset_ids=["not-an-asset-in-this-entry"],
        )
        with pytest.raises(InputValidationError, match="important_asset_not_in_context"):
            ProjectIncrementalService().preview(session, request)
        assert not session.new and not session.dirty


def test_confirmation_rechecks_target_after_waiting_for_database_writer(seeded_records, tmp_path):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    import httpx
    import pytest

    from app.core.errors import ConflictError
    from app.schemas.assessment import AssessmentStartRequest
    from app.schemas.collection_queue import CollectionOptions
    from app.schemas.scan import ScanOptions
    from tests.test_url_scan_pipeline import _service

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid, eid, tid, url = project.id, entry.id, target.id, entry.entry_url
    sent = []

    def respond(request):
        sent.append(request.url.path)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="asset")

    scans = _service(tmp_path, respond)
    try:
        scans.start_assessment(
            AssessmentStartRequest(
                url=url,
                target_id=tid,
                project_entry_id=eid,
                options=ScanOptions(durable=CollectionOptions(min_interval_ms=0)),
            )
        )
        planner = ProjectIncrementalService(scans=scans)
        request = ProjectIncrementalRequest(project_id=pid, entry_id=eid, stale_after_days=0)
        with session_scope() as session:
            preview = planner.preview(session, request)
        started = Event()

        def confirm():
            started.set()
            with session_scope() as session:
                return planner.start(session, request, preview["token"])

        with ThreadPoolExecutor(max_workers=1) as pool:
            with session_scope() as writer:
                writer.get(Target, tid).type = "changed-during-confirmation"
                writer.flush()
                future = pool.submit(confirm)
                assert started.wait(5)
                # SQLite's reserved writer lock permits the preview's reads but
                # blocks task creation until the configuration commit below.
                time.sleep(0.3)
                assert not future.done()
            with pytest.raises(ConflictError):
                future.result(timeout=10)
        assert sent == ["/"]
        assert len(scans.list_scans()) == 1
    finally:
        scans.shutdown()
