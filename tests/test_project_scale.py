"""Synthetic scale evidence; timings are an environment baseline, not an SLA."""

import json
import time
from datetime import datetime, timezone

from sqlalchemy import insert

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanAsset
from app.models.target import Target
from app.schemas.projects import ProjectQuery
from app.services.project_catalog import ProjectCatalogService
from app.services.project_export import export_project
from app.services.project_ingestion import ProjectIngestionService
from tests.helpers.project_assets import import_request, project_fixture, source_run


def test_ten_thousand_observations_paginate_and_export_exactly(seeded_records):
    start = time.perf_counter()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target)
        session.execute(
            insert(ScanAsset),
            [
                dict(
                    scan_run_id=run.id,
                    context_id=run.contexts[0].id,
                    asset_type="page",
                    method="GET",
                    url=f"http://localhost:8111/bulk/{i % 100}?item={i}",
                    status_code=200,
                    discovered_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                    attributes={},
                )
                for i in range(9999)
            ],
        )
        session.flush()
        service = ProjectIngestionService()
        preview = service.preview(session, project.id, import_request(entry, run))
        assert preview.observation_count == 10000
        membership = service.confirm(session, project.id, preview.token)
        assert service.confirm(session, project.id, preview.token).id == membership.id
        indexed = time.perf_counter()
        query = ProjectQuery(view="observations")
        catalog = ProjectCatalogService()
        ids = []
        for page in range(1, 51):
            result = catalog.list(
                session, project.id, query.model_copy(update={"page": page, "page_size": 200})
            )
            ids.extend(r["id"] for r in result.items)
        assert len(ids) == len(set(ids)) == 10000
        export = json.loads(export_project(session, project.id, query, "json"))
        assert export["exported_count"] == 10000
        assert {r["id"] for r in export["items"]} == set(ids)
        assert export["counts"]["assets"] == 101
        print(
            f"scale observations=10000 index_seconds={indexed - start:.3f} "
            f"query_export_seconds={time.perf_counter() - indexed:.3f}"
        )
