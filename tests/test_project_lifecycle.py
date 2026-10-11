import pytest

from app.core.errors import ConflictError
from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.projects import ProjectSourceRef
from app.services.project_ingestion import ProjectIngestionService
from app.services.project_lifecycle import ProjectLifecycleService
from tests.helpers.project_assets import project_fixture, source_run


@pytest.mark.parametrize("terminal_before_attach", [False, True])
def test_attach_before_or_after_completion_and_reconcile(seeded_records, terminal_before_attach):
    lifecycle = ProjectLifecycleService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target)
        run.status = "completed" if terminal_before_attach else "running"
        ref = ProjectSourceRef(source="scan", run_id=run.id)
        membership = lifecycle.attach(session, entry.id, ref)
        assert lifecycle.attach(session, entry.id, ref).id == membership.id
        project_id, membership_id = project.id, membership.id
        run.status = "completed"
    lifecycle.reconcile(ref)
    lifecycle.reconcile(ref)
    with session_scope() as session:
        rows = ProjectIngestionService().runs(session, project_id)
        assert len(rows) == 1
        assert rows[0].status == "complete"
        assert rows[0].observation_count == 1
        assert lifecycle.retry(membership_id).id == membership_id


def test_binding_failure_is_separate_from_source_status(seeded_records):
    lifecycle = ProjectLifecycleService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target)
        ref = ProjectSourceRef(source="scan", run_id=run.id)
        membership = lifecycle.attach(session, entry.id, ref)
        mid, pid = membership.id, project.id
        target.base_url = "http://localhost:8222/"
    lifecycle.reconcile(ref)
    with session_scope() as session:
        result = ProjectIngestionService().runs(session, pid)[0]
        assert result.status == "failed"
        assert result.error_code == "project_index_failed"
        assert lifecycle.sources.source(session, ref).status == "completed"
    with pytest.raises(ConflictError, match="entry_binding_changed"):
        lifecycle.retry(mid)


def test_real_quick_pipeline_indexes_selected_project(seeded_records, tmp_path):
    import httpx

    from app.schemas.assessment import AssessmentStartRequest
    from tests.test_url_scan_pipeline import _service

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid, eid, tid, url = project.id, entry.id, target.id, entry.entry_url
    service = _service(
        tmp_path,
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"}, text="<title>Project fixture</title>"
        ),
    )
    try:
        result = service.start_assessment(
            AssessmentStartRequest(
                url=url,
                target_id=tid,
                project_entry_id=eid,
            )
        )
        with session_scope() as session:
            membership = ProjectIngestionService().runs(session, pid)
            assert len(membership) == 1
            assert membership[0].run_id == result.id
            assert membership[0].status == "complete"
            assert membership[0].observation_count > 0
    finally:
        service.shutdown()


def test_scan_preview_rejects_stale_project_binding(seeded_records):
    from app.core.settings import get_settings
    from app.schemas.assessment import AssessmentStartRequest
    from app.services.scan_submission_preview import build_preview

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        _, entry = project_fixture(session, target)
        request = AssessmentStartRequest(
            url=entry.entry_url, target_id=target.id, project_entry_id=entry.id
        )
        assert build_preview(session, request, get_settings()).can_submit
        entry.enabled = False
        assert not build_preview(session, request, get_settings()).can_submit


def test_identity_failure_still_indexes_known_coverage(seeded_records, tmp_path):
    from app.schemas.identity import IdentityCollectionRequest
    from app.services.identity_collection import IdentityCollectionService
    from app.services.scan_application import InlineScanDispatcher
    from app.services.scan_reporting import ScanReportService

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid, eid, tid, url = project.id, entry.id, target.id, entry.entry_url
    service = IdentityCollectionService(
        dispatcher=InlineScanDispatcher(), report_service=ScanReportService(tmp_path / "reports")
    )
    with session_scope() as session:
        run_id = service.start(
            session,
            IdentityCollectionRequest(
                url=url,
                target_id=tid,
                project_entry_id=eid,
                profile_ids=[seeded_records["credential"].id],
            ),
        )
    with session_scope() as session:
        membership = ProjectIngestionService().runs(session, pid)
        assert len(membership) == 1
        assert membership[0].run_id == run_id
        assert membership[0].status == "complete"
        assert membership[0].snapshot["scope"]["completeness"] == "incomplete"


def test_identity_preview_binds_project_revision(seeded_records, tmp_path):
    from app.core.errors import GardenError
    from app.schemas.identity import IdentityCollectionRequest
    from app.services.identity_collection import IdentityCollectionService
    from app.services.identity_preview import IdentityPreviewService
    from app.services.scan_application import InlineScanDispatcher
    from app.services.scan_reporting import ScanReportService

    collection = IdentityCollectionService(
        dispatcher=InlineScanDispatcher(), report_service=ScanReportService(tmp_path / "reports")
    )
    previews = IdentityPreviewService(collection)
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        request = IdentityCollectionRequest(
            url=entry.entry_url,
            target_id=target.id,
            include_anonymous=True,
            project_entry_id=entry.id,
        )
        preview = previews.preview(session, request)
        assert preview["project_entry_id"] == entry.id
        ProjectIngestionService().projects.bump(session, project.id)
        with pytest.raises(GardenError):
            previews.start(session, preview["preview_token"])


def test_deduplicated_task_rechecks_completion_after_membership_commit(seeded_records, tmp_path):
    import httpx

    from app.models.scan_run import ScanRun
    from app.schemas.assessment import AssessmentStartRequest
    from tests.test_scan_preview_pages import HoldingDispatcher
    from tests.test_url_scan_pipeline import _service

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid, eid, tid, url = project.id, entry.id, target.id, entry.entry_url
    service = _service(tmp_path, lambda request: httpx.Response(200, text="fixture"))
    service.dispatcher = HoldingDispatcher()
    try:
        original = service.start_assessment(AssessmentStartRequest(url=url, target_id=tid))
        # A worker can reach terminal state between dedup selection and attachment.
        # Keep its selected active key to reproduce the late-attachment branch.
        from sqlalchemy import update

        with session_scope() as session:
            session.execute(
                update(ScanRun).where(ScanRun.id == original.id).values(status="completed")
            )
        duplicate = service.start_assessment(
            AssessmentStartRequest(url=url, target_id=tid, project_entry_id=eid)
        )
        assert duplicate.id == original.id
        with session_scope() as session:
            runs = ProjectIngestionService().runs(session, pid)
            assert len(runs) == 1
            assert runs[0].status == "complete"
    finally:
        service.shutdown()


@pytest.mark.parametrize("linked_at_start", [False, True])
def test_identity_query_entry_binds_without_exposing_query_values(
    seeded_records, tmp_path, linked_at_start
):
    from app.models.scan_run import ScanRun
    from app.schemas.identity import IdentityCollectionRequest
    from app.schemas.projects import ProjectEntryCreate
    from app.services.identity_collection import IdentityCollectionService
    from app.services.projects import ProjectService
    from tests.helpers.project_assets import import_request
    from tests.test_scan_preview_pages import HoldingDispatcher

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, initial = project_fixture(session, target)
        url = target.base_url + "?tenant=demo&token=SYNTHETIC_SECRET"
        entry = ProjectService().update_entry(
            session,
            initial.id,
            ProjectEntryCreate(target_id=target.id, entry_url=url, name="Query entry"),
            project.revision,
        )
        pid, eid, tid = project.id, entry.id, target.id
    service = IdentityCollectionService(dispatcher=HoldingDispatcher())
    with session_scope() as session:
        rid = service.start(
            session,
            IdentityCollectionRequest(
                url=url,
                target_id=tid,
                include_anonymous=True,
                project_entry_id=eid if linked_at_start else None,
            ),
        )
    with session_scope() as session:
        run = session.get(ScanRun, rid)
        assert "SYNTHETIC_SECRET" not in run.normalized_url
        assert "tenant=demo" not in run.normalized_url
        run.status = "failed"
    with session_scope() as session:
        run = session.get(ScanRun, rid)
        entry = ProjectService().get_entry(session, pid, eid)
        ingestion = ProjectIngestionService()
        preview = ingestion.preview(session, pid, import_request(entry, run))
        indexed = ingestion.confirm(session, pid, preview.token)
        assert indexed.status == "complete"
        assert "SYNTHETIC_SECRET" not in str(indexed.snapshot)
        wrong = ProjectService().add_entry(
            session,
            pid,
            ProjectEntryCreate(
                target_id=tid,
                entry_url=url.replace("tenant=demo", "tenant=other"),
                name="Other tenant",
            ),
        )
        with pytest.raises(ConflictError, match="source_entry_mismatch"):
            ingestion.preview(session, pid, import_request(wrong, run))
        from app.schemas.scan import ScanRunView
        from app.services.project_presentation import run_view

        assert "entry_url_digest" not in ScanRunView.model_fields
        assert run.entry_url_digest not in str(run_view(indexed))
