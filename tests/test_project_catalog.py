from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.projects import ProjectQuery
from app.services.project_catalog import ProjectCatalogService
from app.services.project_ingestion import ProjectIngestionService
from tests.helpers.project_assets import import_request, project_fixture, source_run


def test_history_uses_source_time_and_stable_subjects(seeded_records):
    ingestion = ProjectIngestionService()
    catalog = ProjectCatalogService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        for day, code in [(3, 404), (1, 200), (3, 403)]:
            run = source_run(session, target, day=day, status_code=code)
            preview = ingestion.preview(session, project.id, import_request(entry, run))
            ingestion.confirm(session, project.id, preview.token)
        page = catalog.list(session, project.id, ProjectQuery())
        assert page.matched == 1
        assert page.counts["observations"] == 3
        asset = page.items[0]
        assert asset["first_seen"].startswith("2026-10-01")
        assert asset["last_seen"].startswith("2026-10-03")
        assert sorted(r["record"]["status_codes"] for r in asset["latest_observations"]) == [
            [403],
            [404],
        ]
        detail = catalog.detail(session, project.id, asset["id"])
        assert detail.history.matched == 3
        empty = catalog.list(session, project.id, ProjectQuery(q="no-match"))
        assert empty.matched == 0
        assert empty.counts["observations"] == 3
        history = catalog.list(session, project.id, ProjectQuery(view="observations", page_size=1))
        assert history.matched == 3
        assert len(history.items) == 1


def test_source_deletion_preserves_snapshot_and_marks_unavailable(seeded_records):
    ingestion = ProjectIngestionService()
    catalog = ProjectCatalogService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target)
        preview = ingestion.preview(session, project.id, import_request(entry, run))
        ingestion.confirm(session, project.id, preview.token)
        before = catalog.list(session, project.id, ProjectQuery(view="observations"))
        assert before.items[0]["source_available"] is True
        session.delete(run)
        session.flush()
        after = catalog.list(session, project.id, ProjectQuery(view="observations"))
        assert after.items[0]["source_available"] is False
        assert after.items[0]["record"] == before.items[0]["record"]


def test_observation_filters_apply_before_subject_count(seeded_records):
    from datetime import datetime, timezone

    from app.services.project_curation import ProjectCurationService

    ingestion, catalog = ProjectIngestionService(), ProjectCatalogService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        runs = []
        for day, path in [(1, "/first"), (2, "/first"), (3, "/second")]:
            run = source_run(session, target, day=day, path=path)
            preview = ingestion.preview(session, project.id, import_request(entry, run))
            runs.append(ingestion.confirm(session, project.id, preview.token))
        page = catalog.list(session, project.id, ProjectQuery(sort="url"))
        ProjectCurationService().annotate(
            session, project.id, page.items[0]["id"], ["reviewed"], "", page.revision
        )
        for changes, expected in [
            ({"site": "http://localhost:8111"}, 2),
            ({"site": "http://other.test"}, 0),
            ({"method": "POST"}, 0),
            ({"kind": "page"}, 2),
            ({"tag": "reviewed"}, 1),
            ({"tag": "missing"}, 0),
            ({"q": "/first"}, 1),
            ({"q": "%"}, 0),
            ({"project_run_id": runs[1].id}, 1),
            ({"seen_from": datetime(2026, 10, 2, tzinfo=timezone.utc)}, 2),
            ({"seen_to": datetime(2026, 10, 1, tzinfo=timezone.utc)}, 1),
        ]:
            filtered = catalog.list(session, project.id, ProjectQuery(**changes))
            assert filtered.matched == expected, changes
            assert filtered.counts["observations"] == 3


def test_same_route_keeps_anonymous_401_and_user_200_after_failed_login(seeded_records):
    from sqlalchemy import select

    from app.models.scan_run import ScanAsset
    from app.schemas.projects import ProjectQuery
    from app.services.project_comparison import ProjectComparisonService
    from tests.test_project_comparison import ingest

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        anonymous = source_run(session, target, day=1, status_code=401)
        ingest(session, project, entry, anonymous)
        user = source_run(session, target, day=2, profile_id=seeded_records["credential"].id)
        before = ingest(session, project, entry, user)
        failed = source_run(
            session,
            target,
            day=3,
            profile_id=seeded_records["credential"].id,
            completeness="incomplete",
        )
        failed.status = "failed"
        session.delete(session.scalar(select(ScanAsset).where(ScanAsset.scan_run_id == failed.id)))
        session.flush()
        after = ingest(session, project, entry, failed)
        result = ProjectCatalogService().list(session, project.id, ProjectQuery())
        assert result.counts["assets"] == 1
        assert result.counts["observations"] == 2
        latest = result.items[0]["latest_observations"]
        assert {tuple(o["record"]["status_codes"]) for o in latest} == {(401,), (200,)}
        assert len({o["identity_key"] for o in latest}) == 2
        comparison = ProjectComparisonService().compare(session, project.id, before.id, after.id)
        assert comparison.items[0]["status"] == "unknown"
        assert comparison.counts["not_observed"] == 0
