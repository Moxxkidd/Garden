"""Public query/export parity across the four project views."""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanAsset
from app.models.target import Target
from app.schemas.projects import ProjectQuery
from app.services.project_catalog import ProjectCatalogService
from app.services.project_curation import ProjectCurationService
from app.services.project_export import export_project
from tests.helpers.project_assets import project_fixture, source_run
from tests.test_project_comparison import ingest


@pytest.mark.parametrize("view", ["assets", "observations", "candidates", "relations"])
def test_filters_sort_pagination_and_export_agree(seeded_records, view):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        runs = []
        for day, name in [(1, "zebra"), (2, "alpha")]:
            source = source_run(session, target, day=day, path="/" + name)
            asset = session.scalar(select(ScanAsset).where(ScanAsset.scan_run_id == source.id))
            asset.attributes = {
                "discovery": {
                    "version": 1,
                    "sources": [{"kind": "html_link", "url": target.base_url}],
                }
            }
            source.asset_metadata = {
                "version": 2,
                "candidates": [
                    {
                        "url": target.base_url + name + "-hint",
                        "method": "GET",
                        "asset_type": "page",
                        "sources": [{"kind": "js_literal", "url": target.base_url + "app.js"}],
                    }
                ],
            }
            runs.append(ingest(session, project, entry, source))
        catalog = ProjectCatalogService()
        subjects = catalog.list(session, project.id, ProjectQuery(sort="url"))
        selected = subjects.items[0]["id"]
        ProjectCurationService().annotate(
            session, project.id, selected, ["reviewed"], "", subjects.revision
        )
        total = 4 if view == "relations" else 2
        cases = [
            ({}, total),
            ({"site": "http://localhost:8111"}, total),
            ({"site": "http://different.invalid"}, 0),
            ({"method": "GET"}, total),
            ({"method": "POST"}, 0),
            ({"kind": "script_hint" if view == "relations" else "page"}, 2),
            ({"q": "zebra"}, 2 if view == "relations" else 1),
            ({"identity_key": "unproven-account"}, 0),
            ({"project_run_id": runs[1].id}, 2 if view == "relations" else 1),
            ({"tag": "reviewed"}, 0 if view == "candidates" else 1),
            ({"asset_id": selected}, 0 if view == "candidates" else 1),
            ({"original_asset_id": selected}, 0 if view == "candidates" else 1),
            (
                {"seen_from": datetime(2026, 10, 2, tzinfo=timezone.utc)},
                0 if view == "candidates" else 1,
            ),
            (
                {"seen_to": datetime(2026, 10, 1, tzinfo=timezone.utc)},
                0 if view == "candidates" else 1,
            ),
        ]
        for filters, expected in cases:
            query = ProjectQuery(view=view, page_size=1, **filters)
            page = catalog.list(session, project.id, query)
            assert page.matched == expected, (view, filters)
            assert page.counts == {"assets": 2, "observations": 2, "candidates": 2, "relations": 4}
            exported = json.loads(export_project(session, project.id, query, "json"))
            assert exported["exported_count"] == expected, (view, filters)
        for sort in ("id", "url", "last_seen"):
            ascending = catalog.list(session, project.id, ProjectQuery(view=view, sort=sort))
            descending = catalog.list(
                session, project.id, ProjectQuery(view=view, sort=sort, order="desc")
            )
            assert [r["id"] for r in ascending.items] == [
                r["id"] for r in reversed(descending.items)
            ]
            paged = [
                catalog.list(
                    session, project.id, ProjectQuery(view=view, sort=sort, page=n, page_size=1)
                ).items[0]["id"]
                for n in range(1, total + 1)
            ]
            assert paged == [r["id"] for r in ascending.items]
