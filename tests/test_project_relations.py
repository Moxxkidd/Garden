from sqlalchemy import select

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanAsset
from app.models.target import Target
from app.schemas.projects import ProjectQuery
from app.services.project_catalog import ProjectCatalogService
from app.services.project_ingestion import ProjectIngestionService
from tests.helpers.project_assets import import_request, project_fixture, source_run


def test_saved_page_and_script_sources_do_not_invent_observed_targets(seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target)
        row = session.scalar(select(ScanAsset).where(ScanAsset.scan_run_id == run.id))
        row.attributes = {
            "discovery": {
                "version": 1,
                "sources": [{"kind": "html_link", "url": target.base_url + "page?token=SECRET"}],
            }
        }
        run.asset_metadata = {
            "version": 2,
            "candidates": [
                {
                    "url": target.base_url + "pending",
                    "method": "GET",
                    "asset_type": "page",
                    "sources": [{"kind": "js_literal", "url": target.base_url + "app.js"}],
                }
            ],
        }
        service = ProjectIngestionService()
        preview = service.preview(session, project.id, import_request(entry, run))
        service.confirm(session, project.id, preview.token)
        service.confirm(session, project.id, preview.token)
        relations = ProjectCatalogService().list(
            session, project.id, ProjectQuery(view="relations")
        )
        assert relations.matched == 2
        assert {r["kind"] for r in relations.items} == {"page_reference", "script_hint"}
        assert "SECRET" not in relations.model_dump_json()
        script = next(r for r in relations.items if r["kind"] == "script_hint")
        assert script["to_asset_id"] is None
        assert script["detail"]["to_state"] == "candidate"
        assert relations.counts["assets"] == 1
        assert (
            ProjectCatalogService()
            .list(session, project.id, ProjectQuery(view="relations", site="http://localhost:8111"))
            .matched
            == 2
        )


def test_candidate_identity_and_time_filters_do_not_silently_return_all(seeded_records):
    from datetime import datetime, timezone

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target)
        run.asset_metadata = {
            "version": 2,
            "candidates": [
                {
                    "url": target.base_url + "pending",
                    "method": "GET",
                    "asset_type": "page",
                }
            ],
        }
        service = ProjectIngestionService()
        preview = service.preview(session, project.id, import_request(entry, run))
        service.confirm(session, project.id, preview.token)
        catalog = ProjectCatalogService()
        assert catalog.list(session, project.id, ProjectQuery(view="candidates")).matched == 1
        assert (
            catalog.list(
                session, project.id, ProjectQuery(view="candidates", identity_key="missing")
            ).matched
            == 0
        )
        assert (
            catalog.list(
                session,
                project.id,
                ProjectQuery(
                    view="candidates", seen_from=datetime(2026, 1, 1, tzinfo=timezone.utc)
                ),
            ).matched
            == 0
        )


def test_redirect_relations_use_saved_pairs_and_redact_queries(seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target)
        asset = session.scalar(select(ScanAsset).where(ScanAsset.scan_run_id == run.id))
        asset.attributes = {
            "catalog_redirects": [
                {
                    "requested_url": target.base_url + "old?token=SECRET",
                    "final_url": target.base_url + "asset",
                }
            ]
        }
        service = ProjectIngestionService()
        preview = service.preview(session, project.id, import_request(entry, run))
        service.confirm(session, project.id, preview.token)
        relations = ProjectCatalogService().list(
            session, project.id, ProjectQuery(view="relations")
        )
        assert relations.matched == 1
        assert relations.items[0]["kind"] == "redirect"
        assert relations.items[0]["detail"]["from_state"] == "unresolved"
        assert relations.items[0]["to_asset_id"] is not None
        assert "SECRET" not in relations.model_dump_json()


def test_missing_saved_relations_reports_unknown_not_no_dependencies(seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        service = ProjectIngestionService()
        preview = service.preview(
            session, project.id, import_request(entry, source_run(session, target))
        )
        service.confirm(session, project.id, preview.token)
        result = ProjectCatalogService().list(session, project.id, ProjectQuery(view="relations"))
        assert result.matched == 0
        assert any("不代表不存在依赖" in note for note in result.limitations)


def test_relation_filters_use_its_own_source_observation(seeded_records):
    from datetime import datetime, timezone

    from tests.test_project_comparison import ingest

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        old = source_run(session, target, day=1)
        asset = session.scalar(select(ScanAsset).where(ScanAsset.scan_run_id == old.id))
        asset.attributes = {
            "discovery": {"version": 1, "sources": [{"kind": "html_link", "url": target.base_url}]}
        }
        ingest(session, project, entry, old)
        ingest(
            session,
            project,
            entry,
            source_run(session, target, day=2, profile_id=seeded_records["credential"].id),
        )
        catalog = ProjectCatalogService()
        assert catalog.list(session, project.id, ProjectQuery(view="relations")).matched == 1
        assert (
            catalog.list(
                session,
                project.id,
                ProjectQuery(
                    view="relations", seen_from=datetime(2026, 10, 2, tzinfo=timezone.utc)
                ),
            ).matched
            == 0
        )
        assert (
            catalog.list(
                session,
                project.id,
                ProjectQuery(
                    view="relations",
                    identity_key=f"target:{target.id}:profile:{seeded_records['credential'].id}",
                ),
            ).matched
            == 0
        )
