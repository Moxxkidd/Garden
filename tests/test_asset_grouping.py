"""Route grouping must preserve observations and avoid claims lost to redaction."""

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli.main import app as cli
from app.db.bootstrap import session_scope
from app.models.scan_run import ScanAsset
from app.schemas.assets import AssetQuery
from app.services.asset_catalog import AssetCatalogService
from app.services.context_collection import ContextCollectionService, ObservedRequest

pytest_plugins = ["tests.helpers.asset_catalog"]


def test_grouped_view_preserves_both_parameter_observations(asset_scan):
    with session_scope() as session:
        result = AssetCatalogService().list(
            session, AssetQuery(source="scan", run_id=asset_scan[0], view="grouped")
        )
    assert result.view == "grouped"
    assert result.total == 4
    group = next(i for i in result.items if i.kind == "page")
    assert group.observation_count == 2
    assert len(group.observations) == 2
    assert group.status_codes == [200, 403]
    assert group.variant_count is None
    assert group.rule_version == "route-v1"
    assert "不代表" in result.count_note
    assert "SECRET" not in result.model_dump_json()
    assert set(group.evidence_ids) == {asset_scan[2]}


def test_grouping_keeps_origin_method_path_and_query_multiplicity(asset_scan):
    with session_scope() as session:
        original = session.get(ScanAsset, asset_scan[1])
        for url, method in [
            ("https://other.test/a?token=x", "GET"),
            ("http://site.test/a?token=x", "GET"),
            ("https://site.test:8443/a?token=x", "GET"),
            ("https://site.test/a/?token=x", "GET"),
            ("https://site.test/a?token=x&token=y", "GET"),
            ("https://site.test/a%2Fb?token=x", "GET"),
            ("https://site.test/a/b?token=x", "GET"),
            ("https://site.test/a?token=x", "POST"),
            ("https://SITE.test:443/a?token=another", "GET"),
        ]:
            session.add(
                ScanAsset(
                    scan_run_id=original.scan_run_id,
                    context_id=original.context_id,
                    asset_type="page",
                    url=url,
                    method=method,
                    discovered_at=original.discovered_at,
                )
            )
        session.flush()
        result = AssetCatalogService().select(
            session, AssetQuery(source="scan", run_id=asset_scan[0], view="grouped", kind="page")
        )
    assert result.matched == 9
    group = next(i for i in result.items if i.observation_count == 3)
    assert len(group.observations) == 3


def test_request_dedup_keeps_query_values_even_with_identical_responses():
    service = ContextCollectionService(gateway=None)

    def request(url):
        return ObservedRequest(
            method="GET",
            url=url,
            headers={},
            body=None,
            status_code=200,
            response_headers={"content-type": "application/json"},
            response_text='{"ok":true}',
        )

    a = service._request_fingerprint(request("https://a.test/api?id=1"))
    b = service._request_fingerprint(request("https://a.test/api?id=2"))
    assert a != b
    assert a.startswith("v2:")
    assert a == service._request_fingerprint(request("https://a.test/api?id=1"))


def test_grouped_surfaces_and_full_export_agree(app, asset_scan):
    query = f"source=scan&run_id={asset_scan[0]}&view=grouped&page_size=1"
    with TestClient(app) as client:
        response = client.get("/api/assets?" + query)
        assert response.status_code == 200
        payload = response.json()
        assert payload["view"] == "grouped"
        assert payload["total"] == 4
        export = client.get("/api/assets/export?" + query).json()
        assert export["exported_count"] == 4
        html = client.get("/assets?" + query).text
        assert "归并资产" in html
        assert "view=grouped" in html
        invalid = client.get("/api/assets?" + query.replace("grouped", "bad"))
        assert invalid.status_code == 400
    result = CliRunner().invoke(
        cli,
        [
            "assets",
            "list",
            "--source",
            "scan",
            "--run-id",
            str(asset_scan[0]),
            "--view",
            "grouped",
            "--page-size",
            "1",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["items"] == payload["items"]


def test_captured_variants_keep_response_observations_and_source_links(tmp_path):
    from app.db.bootstrap import get_session
    from app.services.session_storage import SessionStorageService
    from tests.helpers.assessment import make_authenticated_run

    class Gateway:
        def collect(self, run, context):
            def req(value, status, response):
                return ObservedRequest(
                    method="GET",
                    url=f"https://app.example/api?id={value}",
                    headers={"authorization": "Bearer HEADER_SECRET"},
                    body=None,
                    status_code=status,
                    response_headers={"content-type": "application/json"},
                    response_text=response,
                )

            a = req("VALUE_SECRET", 200, '{"ok":true}')
            b = req("SECOND_SECRET", 200, '{"ok":true}')
            c = req("VALUE_SECRET", 403, '{"error":true}')
            return [], [a, b, c, a]

    with get_session() as session:
        run = make_authenticated_run(session)
        context = next(c for c in run.contexts if c.kind == "user")
        service = ContextCollectionService(
            gateway=Gateway(), storage_service=SessionStorageService(tmp_path / "captures")
        )
        summary = service.collect(session, run, context)
        assert summary.request_count == 3
        result = AssetCatalogService().select(
            session, AssetQuery(source="scan", run_id=run.id, view="grouped")
        )
        group = result.items[0]
        assert group.status_codes == [200, 403]
        assert group.variant_count == 2
        assert len(group.request_ids) == 3
        assert sorted(len(v.request_ids) for v in group.variants) == [1, 2]
        assert {o.status_code for v in group.variants for o in v.observations} == {200, 403}
        assert {o.request_id for v in group.variants for o in v.observations} == set(
            group.request_ids
        )
        assert "SECRET" not in result.model_dump_json()
        assert "fingerprint" not in result.model_dump_json()
        assert not session.dirty


def test_unknown_method_and_invalid_urls_are_isolated(asset_inventory):
    with session_scope() as session:
        result = AssetCatalogService().select(
            session, AssetQuery(source="inventory", run_id=asset_inventory, view="grouped")
        )
    assert result.total == 2
    assert all(i.variant_count is None for i in result.items)
    assert all(i.rule_version == "route-v1" for i in result.items)


def test_filter_counts_and_export_include_only_matched_observations(app, asset_scan):
    with TestClient(app) as client:
        base = f"source=scan&run_id={asset_scan[0]}&view=grouped"
        full = client.get("/api/assets?" + base).json()
        filtered = client.get("/api/assets?" + base + "&q=1%2B1").json()
        assert filtered["matched"] == 1
        assert filtered["matched_observation_count"] == 1
        group = filtered["items"][0]
        assert group["observation_count"] == 1
        assert group["asset_id"] == next(
            i["asset_id"] for i in full["items"] if i["kind"] == "page"
        )
        assert group["observations"][0]["title"] == "=1+1"
        csv = client.get("/api/assets/export?" + base + "&format=csv&page_size=1")
        import csv as csv_module
        import io

        rows = list(csv_module.DictReader(io.StringIO(csv.text)))
        assert len(rows) == 4
        assert all(r["rule_version"] == "route-v1" for r in rows)
        assert sum(len(json.loads(r["observations"])) for r in rows) == 5


def test_collector_keeps_origins_paths_and_methods_separate(tmp_path):
    from app.db.bootstrap import get_session
    from app.services.session_storage import SessionStorageService
    from tests.helpers.assessment import make_authenticated_run

    class Gateway:
        def collect(self, run, context):
            pairs = [
                ("GET", "https://app.example/api"),
                ("GET", "https://other.example/api"),
                ("GET", "https://app.example/api/"),
                ("POST", "https://app.example/api"),
            ]
            return [], [
                ObservedRequest(
                    method=method,
                    url=url,
                    headers={},
                    body=None,
                    status_code=200,
                    response_headers={},
                    response_text="ok",
                )
                for method, url in pairs
            ]

    with get_session() as session:
        run = make_authenticated_run(session)
        user = next(c for c in run.contexts if c.kind == "user")
        service = ContextCollectionService(
            gateway=Gateway(), storage_service=SessionStorageService(tmp_path / "captures")
        )
        summary = service.collect(session, run, user)
        assert summary.asset_count == 4
        groups = AssetCatalogService().select(
            session, AssetQuery(source="scan", run_id=run.id, view="grouped")
        )
        assert groups.total == 4
        assert all(len(g.request_ids) == 1 for g in groups.items)


def test_request_headers_are_not_silently_collapsed():
    from dataclasses import replace

    request = ObservedRequest(
        method="GET",
        url="https://a.test/",
        headers={"x-tenant": "first"},
        body=None,
        status_code=200,
        response_headers={},
        response_text="ok",
    )
    service = ContextCollectionService(gateway=None)
    assert service._request_fingerprint(request) != service._request_fingerprint(
        replace(request, headers={"x-tenant": "second"})
    )


def test_inventory_does_not_merge_origins(asset_inventory):
    from datetime import datetime, timezone

    from app.integrations.inventory.playwright_gateway import ObservedEndpoint
    from app.models.inventory_run import InventoryRun
    from app.services.inventory import InventoryBuildService

    with session_scope() as session:
        run = session.get(InventoryRun, asset_inventory)
        service = InventoryBuildService()
        first = service._upsert_endpoint(
            session,
            run,
            ObservedEndpoint(
                method="GET",
                url="https://a.test/same",
                path="/same",
                status_code=200,
                observed_at=datetime.now(timezone.utc),
            ),
        )
        second = service._upsert_endpoint(
            session,
            run,
            ObservedEndpoint(
                method="GET",
                url="https://b.test/same",
                path="/same",
                status_code=403,
                observed_at=datetime.now(timezone.utc),
            ),
        )
        assert first.id != second.id


def test_resource_types_are_preserved(tmp_path):
    from app.db.bootstrap import get_session
    from app.services.context_collection import ObservedResource
    from app.services.session_storage import SessionStorageService
    from tests.helpers.assessment import make_authenticated_run

    class Gateway:
        def collect(self, run, context):
            return [
                ObservedResource(
                    asset_type=kind,
                    method="GET",
                    url="https://app.example/same",
                    status_code=200,
                    title=None,
                    attributes={},
                )
                for kind in ["page", "endpoint"]
            ], []

    with get_session() as session:
        run = make_authenticated_run(session)
        user = next(c for c in run.contexts if c.kind == "user")
        summary = ContextCollectionService(
            gateway=Gateway(), storage_service=SessionStorageService(tmp_path / "captures")
        ).collect(session, run, user)
        assert summary.asset_count == 2


def test_inventory_keeps_query_shape_from_captured_request_url(asset_inventory):
    from datetime import datetime, timezone

    from app.integrations.inventory.playwright_gateway import ObservedEndpoint
    from app.models.inventory_run import InventoryRun
    from app.services.inventory import InventoryBuildService

    with session_scope() as session:
        run = session.get(InventoryRun, asset_inventory)
        service = InventoryBuildService()
        ids = []
        for query in ["a=SECRET", "a=OTHER_SECRET", "b=SECRET", "a=SECRET&a=SECOND_SECRET"]:
            row = service._upsert_endpoint(
                session,
                run,
                ObservedEndpoint(
                    method="GET",
                    url="https://a.test/query",
                    request_url="https://a.test/query?" + query,
                    path="/query",
                    status_code=200,
                    observed_at=datetime.now(timezone.utc),
                ),
            )
            ids.append(row.id)
            assert "SECRET" not in row.url
        assert ids[0] == ids[1]
        assert len(set(ids)) == 3
        result = AssetCatalogService().select(
            session, AssetQuery(source="inventory", run_id=run.id, view="grouped", q="/query")
        )
        assert result.matched == 3
        assert all(g.variant_count is None for g in result.items)
