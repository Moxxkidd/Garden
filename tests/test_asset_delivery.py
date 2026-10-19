import json

import pytest

from app.core.errors import ConflictError
from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.projects import ProjectQuery
from tests.helpers.project_assets import project_fixture, source_run
from tests.test_project_comparison import ingest


def test_delivery_filters_full_export_and_revision_bound_pages(seeded_records):
    from app.services.asset_delivery import AssetDeliveryService

    delivery = AssetDeliveryService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        for day in (1, 2, 3):
            ingest(session, project, entry, source_run(session, target, day=day))
        pid = project.id
    query = ProjectQuery(view="observations", page_size=1, q="asset")
    with session_scope() as session:
        first = delivery.page(session, pid, query)
        full = json.loads(delivery.export(session, pid, query, "json"))
        assert first["schema_version"] == "delivery-v1"
        assert first["matched"] == 3 and len(first["items"]) == 1
        assert len(full["items"]) == 3 and full["next_cursor"] is None
        second = delivery.page(session, pid, query, cursor=first["next_cursor"])
        assert second["items"][0]["id"] != first["items"][0]["id"]
        aid = first["items"][0]["display_asset_id"]
    from app.services.project_curation import ProjectCurationService

    with session_scope() as session:
        ProjectCurationService().annotate(session, pid, aid, ["new"], "", first["revision"])
    with session_scope() as session:
        with pytest.raises(ConflictError, match="delivery_revision_changed"):
            delivery.page(session, pid, query, cursor=first["next_cursor"])


def test_variants_survive_source_removal_and_legacy_is_unknown(seeded_records):
    from app.models.project import ProjectRun
    from app.models.scan_request import ScanRequest
    from app.models.scan_run import ScanRun
    from app.services.asset_delivery import AssetDeliveryService

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        run = source_run(session, target, path="/api?token=synthetic-secret")
        session.add(
            ScanRequest.from_capture(
                scan_run_id=run.id,
                source_context_id=run.contexts[0].id,
                asset_id=run.assets[0].id,
                method="GET",
                raw_url=run.assets[0].url,
                fingerprint="v2:" + "a" * 40 + ":" + "b" * 40,
                header_names=["Authorization"],
                protected_storage_ref="fixture-not-opened",
            )
        )
        session.flush()
        indexed = ingest(session, project, entry, run)
        result = AssetDeliveryService().page(
            session, project.id, ProjectQuery(), dataset="variants"
        )
        assert result["items"][0]["identity_status"] == "known"
        assert "synthetic-secret" not in json.dumps(result)
        assert "a" * 40 not in json.dumps(result)
        # This fixture simulates source removal, not a product deletion API.
        # View-only request/asset relationships do not order ORM cascades.
        source_id = run.id
        request_ids = [request.id for request in run.requests]
        for request in list(run.requests):
            session.delete(request)
        session.flush()
        session.expire(run, ["requests"])
        session.delete(run)
        session.flush()
        assert session.get(ScanRun, source_id) is None
        assert all(session.get(ScanRequest, request_id) is None for request_id in request_ids)
        after = AssetDeliveryService().page(session, project.id, ProjectQuery(), dataset="variants")
        assert after == result
        stored = session.get(ProjectRun, indexed.id)
        stored.snapshot = {k: v for k, v in stored.snapshot.items() if k != "request_variants"}
        session.flush()
        legacy = AssetDeliveryService().page(
            session, project.id, ProjectQuery(), dataset="variants"
        )
        assert legacy["items"][0]["identity_status"] == "unknown"
        assert legacy["items"][0]["variant"] is None


def test_openapi_separates_origins_redacts_and_does_not_invent_schemas(seeded_records):
    from app.schemas.projects import ProjectEntryCreate
    from app.services.asset_delivery import AssetDeliveryService
    from app.services.projects import ProjectService

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        first = source_run(session, target, path="/api?token=synthetic-secret")
        first.assets[0].asset_type = "endpoint"
        session.flush()
        ingest(session, project, entry, first)
        target.base_url = "http://localhost:8222/"
        other = ProjectService().add_entry(
            session,
            project.id,
            ProjectEntryCreate(target_id=target.id, name="Other", entry_url=target.base_url),
        )
        second = source_run(session, target, path="/api?token=another-secret")
        second.assets[0].asset_type = "endpoint"
        session.flush()
        ingest(session, project, other, second)
        raw = AssetDeliveryService().export(
            session, project.id, ProjectQuery(view="observations"), "openapi"
        )
        result = json.loads(raw)
        assert result["openapi"] == "3.1.1" and result["x-garden-inferred"] is True
        assert result["paths"]["/api"]["get"]["servers"] == [
            {"url": "http://localhost:8111"},
            {"url": "http://localhost:8222"},
        ]
        operation = result["paths"]["/api"]["get"]
        assert operation["parameters"][0]["name"] == "token"
        assert operation["parameters"][0]["schema"] == {}
        assert "security" not in operation and "requestBody" not in operation
        assert "content" not in operation["responses"]["200"]
        assert "synthetic-secret" not in raw and "another-secret" not in raw
        assert len(operation["x-garden-observations"]) == 2


def test_delivery_api_cli_web_schema_and_escaped_summary(app, seeded_records):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from app.cli.main import app as cli
    from app.schemas.delivery import DeliveryDocument
    from app.services.asset_delivery import AssetDeliveryService

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        for day in (1, 2):
            ingest(session, project, entry, source_run(session, target, day=day))
        pid = project.id
        for format in ("html", "markdown"):
            content = AssetDeliveryService().export(
                session, pid, ProjectQuery(q="<script>alert(1)</script>"), format
            )
            assert "<script>" not in content and "&lt;script&gt;" in content
    client = TestClient(app)
    base = f"/api/projects/{pid}/delivery"
    result = client.get(base, params={"dataset": "observations", "page_size": 1}).json()
    assert len(result["items"]) == 1 and result["matched"] == 2
    assert DeliveryDocument.model_validate(result).schema_version == "delivery-v1"
    assert (
        client.get("/api/delivery/schema").json()["properties"]["schema_version"]["const"]
        == "delivery-v1"
    )
    second = client.get(
        base, params={"dataset": "observations", "page_size": 1, "cursor": result["next_cursor"]}
    )
    assert second.status_code == 200 and second.json()["next_cursor"] is None
    wrong = client.get(
        base, params={"dataset": "assets", "page_size": 1, "cursor": result["next_cursor"]}
    )
    assert wrong.status_code == 400
    invalid = client.get(base, params={"cursor": "!invalid!"})
    assert invalid.status_code == 400
    export = client.get(
        base + "/export", params={"dataset": "observations", "page": 2, "page_size": 1}
    )
    assert export.status_code == 200 and export.json()["exported_count"] == 2
    page = client.get(
        f"/projects/{pid}/delivery?dataset=observations&q=asset&before_run_id=&after_run_id="
    )
    assert page.status_code == 200
    assert "全部筛选匹配 2 条" in page.text and "OPENAPI" in page.text
    assert "dataset=observations" in page.text and "q=asset" in page.text
    assets = client.get(f"/projects/{pid}/assets?q=asset&view=observations")
    assert "/delivery?" in assets.text and "q=asset" in assets.text
    cli_result = CliRunner().invoke(
        cli, ["projects", "deliver", str(pid), "--dataset", "observations"]
    )
    assert cli_result.exit_code == 0, cli_result.stdout
    assert json.loads(cli_result.stdout) == export.json()


def test_changes_keep_unknown_and_csv_exports_all_selected_rows(seeded_records):
    import csv
    import io

    from app.services.asset_delivery import AssetDeliveryService

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        before = ingest(session, project, entry, source_run(session, target, day=1))
        after = ingest(
            session,
            project,
            entry,
            source_run(session, target, day=2, completeness="incomplete", path="/other"),
        )
        service = AssetDeliveryService()
        result = service.page(
            session,
            project.id,
            ProjectQuery(),
            dataset="changes",
            before_run_id=before.id,
            after_run_id=after.id,
        )
        assert result["matched"] == 2
        assert {r["status"] for r in result["items"]} == {"unknown"}
        csv_text = service.export(
            session,
            project.id,
            ProjectQuery(page_size=1),
            "csv",
            dataset="changes",
            before_run_id=before.id,
            after_run_id=after.id,
        )
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        assert len(rows) == 2
        assert {json.loads(r["record"])["status"] for r in rows} == {"unknown"}
