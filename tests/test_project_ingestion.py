import pytest

from app.core.errors import ConflictError
from app.db.bootstrap import session_scope
from app.models.target import Target
from app.services.project_ingestion import ProjectIngestionService
from tests.helpers.project_assets import import_request, project_fixture, source_run


def test_import_metrics_distinguish_new_subjects_from_repeat_observations(seeded_records):
    service = ProjectIngestionService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid = project.id
        first_run = source_run(session, target)
        second_run = source_run(session, target, day=2)
        previews = []
        for run in [first_run, second_run]:
            preview = service.preview(session, pid, import_request(entry, run))
            membership = service.confirm(session, pid, preview.token)
            previews.append((membership.id, run.contexts[0].id))
    with session_scope() as session:
        first, second = service.runs(session, pid)
        assert first.snapshot["ingestion_metrics"] == {
            "version": 1,
            "new_subjects": 1,
            "new_observations": 1,
            "contexts": {str(previews[0][1]): {"new_subjects": 1, "new_observations": 1}},
        }
        assert second.snapshot["ingestion_metrics"] == {
            "version": 1,
            "new_subjects": 0,
            "new_observations": 1,
            "contexts": {str(previews[1][1]): {"new_subjects": 0, "new_observations": 1}},
        }
        frozen = dict(first.snapshot["ingestion_metrics"])
        assert service.sync(session, first.id).snapshot["ingestion_metrics"] == frozen


def test_ingestion_retries_preserve_history_and_refuse_changed_source(seeded_records):
    service = ProjectIngestionService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target)
        preview = service.preview(session, project.id, import_request(entry, run))
        assert preview.subject_count == preview.observation_count == 1
        first = service.confirm(session, project.id, preview.token)
        again = service.preview(session, project.id, import_request(entry, run))
        second = service.confirm(session, project.id, again.token)
        assert first.id == second.id
        assert second.observation_count == 1
        run.options = {"max_pages": 2}
        session.flush()
        with pytest.raises(ConflictError, match="source_changed"):
            service.sync(session, second.id)


def test_preview_rejects_changed_project_and_has_no_persisted_membership(seeded_records):
    service = ProjectIngestionService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target)
        preview = service.preview(session, project.id, import_request(entry, run))
        assert service.runs(session, project.id) == []
        target.base_url = "http://localhost:8222/"
        with pytest.raises(ConflictError):
            service.confirm(session, project.id, preview.token)


def test_source_cannot_be_reassigned_to_another_entry(seeded_records):
    from app.schemas.projects import ProjectEntryCreate

    service = ProjectIngestionService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target)
        preview = service.preview(session, project.id, import_request(entry, run))
        service.confirm(session, project.id, preview.token)
        other = service.projects.add_entry(
            session,
            project.id,
            ProjectEntryCreate(target_id=target.id, entry_url=entry.entry_url, name="Other"),
        )
        preview = service.preview(session, project.id, import_request(other, run))
        with pytest.raises(ConflictError, match="source_already_bound_to_entry"):
            service.confirm(session, project.id, preview.token)


def test_failed_import_is_atomic_even_when_caller_commits(seeded_records):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    service = ProjectIngestionService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target)
        pid = project.id
        revision = project.revision
        preview = service.preview(session, pid, import_request(entry, run))
        # Exercise a storage failure after membership and asset creation.
        session.execute(
            text(
                "CREATE TRIGGER reject_observation BEFORE INSERT ON project_observations "
                "BEGIN SELECT RAISE(ABORT, 'fixture storage failure'); END"
            )
        )
        with pytest.raises(IntegrityError, match="fixture storage failure"):
            service.confirm(session, pid, preview.token)
        assert service.runs(session, pid) == []
        assert service.projects.get(session, pid).revision == revision
        session.execute(text("DROP TRIGGER reject_observation"))
    with session_scope() as session:
        assert service.runs(session, pid) == []
        result = service.confirm(session, pid, preview.token)
        assert result.status == "complete"
        assert result.observation_count == 1


def test_caller_rollback_also_rolls_back_successful_import(seeded_records):
    service = ProjectIngestionService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target)
        pid = project.id
        request = import_request(entry, run)
    with pytest.raises(RuntimeError, match="outer transaction cancelled"):
        with session_scope() as session:
            preview = service.preview(session, pid, request)
            service.confirm(session, pid, preview.token)
            raise RuntimeError("outer transaction cancelled")
    with session_scope() as session:
        assert service.runs(session, pid) == []


def test_concurrent_confirmation_conflicts_safely_then_retries_once(seeded_records):
    from sqlalchemy import select

    from app.db.bootstrap import get_engine, get_session
    from app.models.project import Project
    from app.schemas.projects import ProjectQuery
    from app.services.project_catalog import ProjectCatalogService
    from app.services.project_transactions import ensure_read_snapshot

    with get_engine().connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    service = ProjectIngestionService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        preview = service.preview(
            session, project.id, import_request(entry, source_run(session, target))
        )
        pid = project.id
    with get_session() as stale:
        ensure_read_snapshot(stale)
        stale.scalar(select(Project).where(Project.id == pid))
        with session_scope() as winner:
            membership_id = service.confirm(winner, pid, preview.token).id
        with pytest.raises(ConflictError, match="project_concurrent_update"):
            service.confirm(stale, pid, preview.token)
        stale.rollback()
        assert service.confirm(stale, pid, preview.token).id == membership_id
        stale.commit()
    with session_scope() as session:
        assert (
            ProjectCatalogService().list(session, pid, ProjectQuery(view="observations")).matched
            == 1
        )
