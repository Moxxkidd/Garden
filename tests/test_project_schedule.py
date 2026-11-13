from datetime import datetime, timedelta, timezone

import httpx

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.collection_queue import CollectionOptions
from app.schemas.project_schedule import ScheduleRequest
from app.schemas.scan import ScanOptions
from app.services.project_schedule import ProjectScheduleService
from app.services.projects import ProjectService
from tests.helpers.project_assets import project_fixture
from tests.test_url_scan_pipeline import _service


def test_fixed_recheck_is_explicit_bounded_and_never_catches_up(seeded_records, tmp_path):
    now = [datetime(2026, 11, 13, 1, tzinfo=timezone.utc)]
    sent = []
    scans = _service(tmp_path, lambda r: sent.append(str(r.url)) or httpx.Response(200, text="ok"))
    scheduler = ProjectScheduleService(scans=scans, clock=lambda: now[0])
    try:
        with session_scope() as session:
            target = session.get(Target, seeded_records["target"].id)
            project, entry = project_fixture(session, target)
            pid, eid = project.id, entry.id
            assert scheduler.list(session, pid) == []
            req = ScheduleRequest(
                entry_id=eid,
                interval_hours=1,
                start_at=now[0] + timedelta(hours=1),
                options=ScanOptions(durable=CollectionOptions(max_requests=1, min_interval_ms=0)),
            )
            preview = scheduler.preview(session, pid, req)
            row = scheduler.confirm(session, preview["token"])
            sid = row["id"]
            assert row["enabled"] is False
        now[0] += timedelta(hours=1)
        scheduler.tick()
        assert sent == []
        with session_scope() as session:
            preview = scheduler.preview(
                session, pid, req.model_copy(update={"start_at": now[0]}), schedule_id=sid
            )
            scheduler.confirm(session, preview["token"], enable=True)
        scheduler.tick()
        assert len(sent) == 1
        with session_scope() as session:
            current = scheduler.list(session, pid)[0]
            assert current["last_result"] == "started"
            assert current["configuration"]["durable"]["max_requests"] == 1
            from sqlalchemy import select

            from app.models.collection_queue import CollectionQueue
            from app.services.collection_admission import CollectionAdmissionService

            queue = session.scalar(
                select(CollectionQueue).where(CollectionQueue.run_id == current["last_run_id"])
            )
            assert CollectionAdmissionService().summary(session, queue.id)["requests_used"] == 1
        now[0] += timedelta(hours=5, minutes=10)
        scheduler.startup()  # same rule as a real service process restart
        scheduler.tick()
        assert len(sent) == 1
        with session_scope() as session:
            current = scheduler.list(session, pid)[0]
            assert current["last_result"] == "missed"
            assert datetime.fromisoformat(current["next_at"]) > now[0]
            project = ProjectService().get(session, pid)
            ProjectService().archive(session, pid, project.revision)
            assert scheduler.list(session, pid)[0]["enabled"] is False
            ProjectService().restore(session, pid, project.revision)
            assert scheduler.list(session, pid)[0]["enabled"] is False
    finally:
        scans.shutdown()


def test_recheck_skips_active_project_and_pauses_changed_or_expired_identity(seeded_records):
    from app.models.auth_session import AuthSession
    from app.schemas.projects import ProjectEntryUpdate
    from app.services.scan_application import ScanApplicationService
    from tests.test_identity_plans import HoldingDispatcher

    now = [datetime(2026, 11, 13, 1, tzinfo=timezone.utc)]
    scans = ScanApplicationService(dispatcher=HoldingDispatcher())
    scheduler = ProjectScheduleService(scans=scans, clock=lambda: now[0])
    try:
        with session_scope() as session:
            target = session.get(Target, seeded_records["target"].id)
            project, entry = project_fixture(session, target)
            pid, eid = project.id, entry.id
            state = session.get(AuthSession, seeded_records["auth_session"].id)
            state.identity_metadata = {"target_origin": "http://localhost:8111"}
            state.last_validated_at = now[0]
            state.expires_at = now[0] + timedelta(hours=2)
            session.flush()
            for profile in [None, seeded_records["credential"].id]:
                request = ScheduleRequest(
                    entry_id=eid, profile_id=profile, start_at=now[0], interval_hours=1
                )
                p = scheduler.preview(session, pid, request)
                scheduler.confirm(session, p["token"], enable=True)
        scheduler.tick()
        with session_scope() as session:
            rows = scheduler.list(session, pid)
            assert [r["last_result"] for r in rows] == ["started", "skipped_active"]
            assert len(scans.list_scans()) == 1
        now[0] += timedelta(hours=3)
        scheduler.tick()
        with session_scope() as session:
            rows = scheduler.list(session, pid)
            assert rows[0]["last_result"] == "skipped_active"
            assert rows[1]["last_result"] == "schedule_identity_not_ready"
            assert not rows[1]["enabled"]
            assert len(scans.list_scans()) == 1  # no anonymous downgrade and no overlap
            project = ProjectService().get(session, pid)
            ProjectService().update_entry(
                session,
                eid,
                ProjectEntryUpdate(
                    target_id=entry.target_id,
                    name="changed",
                    entry_url=entry.entry_url,
                    expected_revision=project.revision,
                ),
                project.revision,
            )
            assert not scheduler.list(session, pid)[0]["enabled"]
    finally:
        scans.shutdown()
