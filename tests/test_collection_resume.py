import json

import pytest

from app.core.errors import ConflictError
from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.collection_queue import CollectionOptions
from app.schemas.collection_resume import CollectionResumeRequest
from app.services.collection_queue import CollectionQueueService
from app.services.collection_resume import CollectionResumeService
from app.services.collection_storage import CollectionStorage
from tests.helpers.project_assets import source_run


def test_resume_preview_is_readonly_redacted_and_binds_exact_target(seeded_records, tmp_path):
    private = tmp_path / "private"
    queues = CollectionQueueService(storage=CollectionStorage(private))
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = queues.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
        work = queues.enqueue(
            session, qid, target.base_url.rstrip("/") + "/next?token=FIXTURE_SECRET"
        )
        token = queues.lease(session, qid)
        queues.close(session, qid, token)
        target_id = target.id
    before = {p.name: p.read_bytes() for p in private.iterdir()}
    service = CollectionResumeService(queues=queues)
    request = CollectionResumeRequest(queue_id=qid)
    with session_scope() as session:
        revision = queues.get(session, qid)["revision"]
        preview = service.preview(session, request)
        assert preview["work_count"] == 1
        assert preview["mode"] == "known_only"
        assert preview["work_ids"] == [work["id"]]
        assert "FIXTURE_SECRET" not in json.dumps(preview)
        assert queues.get(session, qid)["revision"] == revision
        assert not session.new and not session.dirty
        assert service.confirm_preview(session, request, preview["token"])["work_count"] == 1
    assert before == {p.name: p.read_bytes() for p in private.iterdir()}
    with session_scope() as session:
        target = session.get(Target, target_id)
        target.base_url = target.base_url.rstrip("/") + "/changed"
    with session_scope() as session:
        with pytest.raises(ConflictError, match="collection_binding_changed"):
            service.confirm_preview(session, request, preview["token"])


@pytest.mark.parametrize("with_project", [False, True])
def test_known_work_resume_creates_linked_run_and_preserves_original_report(
    tmp_path, seeded_records, with_project
):
    from pathlib import Path

    import httpx

    from app.schemas.scan import ScanOptions
    from tests.test_url_scan_pipeline import _service

    sent = []

    def handle(request):
        sent.append(request.url.path)
        html = (
            '<a href="/one">one</a><a href="/two">two</a>'
            if request.url.path == "/"
            else '<a href="/new">new</a>'
        )
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    scans = _service(tmp_path, handle)
    try:
        options = ScanOptions(durable=CollectionOptions(max_requests=1, min_interval_ms=0))
        if with_project:
            from app.schemas.assessment import AssessmentStartRequest
            from app.services.project_ingestion import ProjectIngestionService
            from tests.helpers.project_assets import project_fixture

            with session_scope() as session:
                target = session.get(Target, seeded_records["target"].id)
                project, entry = project_fixture(session, target)
                pid, eid, tid, url = project.id, entry.id, target.id, entry.entry_url
            original = scans.start_assessment(
                AssessmentStartRequest(
                    url=url,
                    target_id=tid,
                    project_entry_id=eid,
                    options=options,
                )
            )
            with session_scope() as session:
                before_project = json.loads(
                    json.dumps(ProjectIngestionService().runs(session, pid)[0].snapshot)
                )
        else:
            original = scans.start_scan("http://localhost:8123/", options)
        report = Path(original.report_path).read_bytes()
        resume = CollectionResumeService(scans=scans)
        with session_scope() as session:
            queue = resume.queues.for_run(session, original.id)[0]
            request = CollectionResumeRequest(
                queue_id=queue["id"], options=CollectionOptions(min_interval_ms=0)
            )
            preview = resume.preview(session, request)
        sent.clear()
        with session_scope() as session:
            child_id = resume.start(session, request, preview["token"])
        assert set(sent) == {"/one", "/two"}
        assert Path(original.report_path).read_bytes() == report
        with session_scope() as session:
            child = resume.queues.for_run(session, child_id)[0]
            assert child["parent_id"] == queue["id"]
            assert child["coverage_complete"]
            assert resume.start(session, request, preview["token"]) == child_id
        assert len(sent) == 2
        if with_project:
            with session_scope() as session:
                memberships = ProjectIngestionService().runs(session, pid)
                assert len(memberships) == 2
                assert (
                    next(m for m in memberships if m.run_id == original.id).snapshot
                    == before_project
                )
                assert next(m for m in memberships if m.run_id == child_id).status == "complete"
    finally:
        scans.shutdown()


def test_resume_rechecks_target_even_when_callers_session_has_cached_it(seeded_records, tmp_path):
    queues = CollectionQueueService(storage=CollectionStorage(tmp_path / "private"))
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = queues.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
        queues.enqueue(session, qid, target.base_url)
        lease = queues.lease(session, qid)
        queues.close(session, qid, lease)
        tid = target.id
    service = CollectionResumeService(queues=queues)
    request = CollectionResumeRequest(queue_id=qid)
    with session_scope() as stale:
        cached_target = stale.get(Target, tid)
        preview = service.preview(stale, request)
        with session_scope() as changed:
            changed.get(Target, tid).base_url += "changed"
        assert cached_target is not None
        with pytest.raises(ConflictError, match="collection_binding_changed"):
            service.confirm_preview(stale, request, preview["token"])


def test_resume_dispatch_failure_retains_a_visible_recoverable_child(tmp_path):
    import httpx

    from app.schemas.scan import ScanOptions
    from tests.test_url_scan_pipeline import _service

    class UnavailableDispatcher:
        def submit(self, run_id, callback):
            raise RuntimeError("synthetic dispatcher unavailable")

    scans = _service(
        tmp_path,
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/next">next</a>',
        ),
    )
    try:
        original = scans.start_scan(
            "http://localhost:8123/",
            ScanOptions(
                durable=CollectionOptions(max_requests=1, min_interval_ms=0),
            ),
        )
        resume = CollectionResumeService(scans=scans)
        with session_scope() as session:
            queue = resume.queues.for_run(session, original.id)[0]
            request = CollectionResumeRequest(queue_id=queue["id"])
            preview = resume.preview(session, request)
        scans.dispatcher = UnavailableDispatcher()
        with session_scope() as session:
            child_id = resume.start(session, request, preview["token"])
        assert scans.get_scan(child_id).status == "failed"
        with session_scope() as session:
            child = resume.queues.for_run(session, child_id)[0]
            assert child["counts"] == {"pending": 1}
            assert child["metrics"]["requests_used"] == 0
            assert (
                resume.preview(session, CollectionResumeRequest(queue_id=child["id"]))["work_count"]
                == 1
            )
    finally:
        scans.shutdown()


def test_suppressed_work_requires_explicit_selection_in_new_budget(seeded_records, tmp_path):
    queues = CollectionQueueService(storage=CollectionStorage(tmp_path / "private"))
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = queues.create(session, run.id, run.contexts[0].id, CollectionOptions(max_variants=1))[
            "id"
        ]
        first = queues.enqueue(session, qid, target.base_url + "/list?page=1")
        suppressed = queues.enqueue(session, qid, target.base_url + "/list?page=2")
        assert suppressed["state"] == "suppressed"
        lease = queues.lease(session, qid)
        queues.close(session, qid, lease)
    service = CollectionResumeService(queues=queues)
    with session_scope() as session:
        default = service.preview(session, CollectionResumeRequest(queue_id=qid))
        assert default["work_ids"] == [first["id"]]
        explicit = service.preview(
            session,
            CollectionResumeRequest(
                queue_id=qid,
                work_ids=[suppressed["id"]],
                options=CollectionOptions(max_variants=2),
            ),
        )
        assert explicit["work_ids"] == [suppressed["id"]]
        assert explicit["options"]["max_variants"] == 2
