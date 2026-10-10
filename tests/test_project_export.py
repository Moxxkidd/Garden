import csv
import io
import json

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.projects import ProjectQuery
from app.services.project_export import export_project
from tests.helpers.project_assets import project_fixture, source_run
from tests.test_project_comparison import ingest


def test_export_ignores_page_but_preserves_filters_and_unknown(seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        for day in (1, 2, 3):
            ingest(session, project, entry, source_run(session, target, day=day))
        query = ProjectQuery(view="observations", page=2, page_size=1)
        payload = json.loads(export_project(session, project.id, query, "json"))
        assert payload["schema_version"] == "project-v1"
        assert payload["exported_count"] == 3
        assert len(payload["items"]) == 3
        assert "page" not in payload["filters"]
        rows = list(csv.DictReader(io.StringIO(export_project(session, project.id, query, "csv"))))
        assert len(rows) == 3
        assert {r["exported_count"] for r in rows} == {"3"}
        assert {r["id"] for r in rows} == {str(r["id"]) for r in payload["items"]}


def test_export_stays_on_one_snapshot_during_concurrent_annotation(seeded_records):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import event

    from app.db.bootstrap import get_engine
    from app.services.project_catalog import ProjectCatalogService
    from app.services.project_curation import ProjectCurationService

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        ingest(session, project, entry, source_run(session, target))
        page = ProjectCatalogService().list(session, project.id, ProjectQuery())
        pid, aid, revision = project.id, page.items[0]["id"], page.revision
    engine = get_engine()
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    changed = []
    with ThreadPoolExecutor(max_workers=1) as pool:

        def annotate():
            with session_scope() as writer:
                ProjectCurationService().annotate(
                    writer, pid, aid, [], "new concurrent note", revision
                )

        def after_read(connection, cursor, statement, parameters, context, many):
            if "FROM projects" in statement and not changed:
                changed.append(True)
                pool.submit(annotate).result(timeout=10)

        event.listen(engine, "after_cursor_execute", after_read)
        try:
            with session_scope() as reader:
                exported = json.loads(export_project(reader, pid, ProjectQuery(), "json"))
        finally:
            event.remove(engine, "after_cursor_execute", after_read)
    assert changed
    assert exported["revision"] == revision
    assert exported["items"][0]["note"] == ""
    with session_scope() as reader:
        current = ProjectCatalogService().list(reader, pid, ProjectQuery())
        assert current.revision == revision + 1
        assert current.items[0]["note"] == "new concurrent note"


def test_markdown_summary_links_to_filtered_evidence(seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        ingest(session, project, entry, source_run(session, target))
        text = export_project(
            session, project.id, ProjectQuery(view="observations", q="asset"), "markdown"
        )
        assert f"/projects/{project.id}/assets?" in text
        assert "view=observations" in text
        assert "q=asset" in text


def test_empty_csv_preserves_filter_and_project_metadata(seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, _ = project_fixture(session, target)
        rows = list(
            csv.DictReader(
                io.StringIO(export_project(session, project.id, ProjectQuery(q="missing"), "csv"))
            )
        )
        assert len(rows) == 1
        assert rows[0]["row_kind"] == "metadata"
        assert rows[0]["project_id"] == str(project.id)
        assert rows[0]["exported_count"] == "0"
        assert json.loads(rows[0]["filters"])["q"] == "missing"
