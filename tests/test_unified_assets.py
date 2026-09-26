"""Unified list contracts: provenance and display-safe selection."""

import csv
import io
import json

import pytest
from pydantic import ValidationError

from app.db.bootstrap import session_scope
from app.schemas.assets import AssetQuery
from app.services.asset_catalog import AssetCatalogService
from app.services.asset_export import export_assets

pytest_plugins = ["tests.helpers.asset_catalog"]


def test_scan_preserves_rows_and_unknowns_without_exposing_raw_material(asset_scan):
    run_id, record_id, evidence_id = asset_scan
    with session_scope() as session:
        result = AssetCatalogService().list(session, AssetQuery(source="scan", run_id=run_id))
    assert result.total == result.matched == len(result.items) == 5
    assert result.counts_by_kind == {"page": 2, "endpoint": 1, "static": 1, "file": 1, "other": 0}
    page = next(i for i in result.items if i.record_id == record_id)
    assert page.context == "user"
    assert page.observation == "response_observed"
    assert page.evidence_ids == [evidence_id]
    assert page.source_url == f"/scans/{run_id}"
    assert page.request_count is None
    assert page.discovery_url is None
    assert result.scope.completeness == "missing_admin_context"
    unknown = next(i for i in result.items if i.kind == "endpoint")
    assert unknown.observation == "unknown"
    assert unknown.context == "admin"
    assert unknown.context_completeness == "incomplete"
    assert unknown.status_codes == []
    assert any(i.status_codes == [403] for i in result.items)
    text = result.model_dump_json()
    for secret in [
        "URL_PASSWORD",
        "QUERY_SECRET",
        "OTHER_SECRET",
        "FRAGMENT_SECRET",
        "ATTRIBUTE_SECRET",
        "BODY_SECRET",
        "RESPONSE_SECRET",
    ]:
        assert secret not in text
    assert len({i.asset_id for i in result.items}) == 5


def test_inventory_uses_actual_rows_and_preserves_legacy_limits(asset_inventory):
    with session_scope() as session:
        result = AssetCatalogService().list(
            session, AssetQuery(source="inventory", run_id=asset_inventory)
        )
    assert result.total == result.matched == 2
    assert result.scope.completeness is None
    assert "未知" in result.scope.coverage_note
    assert {i.kind for i in result.items} == {"page", "endpoint"}
    assert {i.context for i in result.items} == {"administrator"}
    page = next(i for i in result.items if i.kind == "page")
    endpoint = next(i for i in result.items if i.kind == "endpoint")
    assert page.observation == "unknown"
    assert page.content_type is None
    assert endpoint.status_codes == [200, 401]
    assert endpoint.request_count == 3
    assert endpoint.evidence_ids is None
    assert "SECRET" not in result.model_dump_json()


def test_filters_are_applied_before_pagination_and_search_only_display_values(asset_scan):
    with session_scope() as session:
        service = AssetCatalogService()
        query = AssetQuery(
            source="scan", run_id=asset_scan[0], kind="page", context="user", page_size=1
        )
        one = service.list(session, query)
        two = service.list(session, query.model_copy(update={"page": 2}))
        missing = service.list(session, query.model_copy(update={"q": "QUERY_SECRET"}))
        unknown = service.list(
            session, AssetQuery(source="scan", run_id=asset_scan[0], observation="unknown")
        )
    assert one.total == 5 and one.matched == two.matched == 2
    assert one.items[0].asset_id != two.items[0].asset_id
    assert one.items[0].url == two.items[0].url
    assert missing.matched == 0
    assert len(unknown.items) == 1


def test_export_contains_all_filtered_pages_and_csv_is_formula_safe(asset_scan):
    query = AssetQuery(source="scan", run_id=asset_scan[0], kind="page", page_size=1, page=2)
    with session_scope() as session:
        payload = json.loads(export_assets(session, query, "json"))
        rows = list(csv.DictReader(io.StringIO(export_assets(session, query, "csv"))))
    assert payload["exported_count"] == payload["matched"] == len(rows) == 2
    assert len(payload["items"]) == 2
    assert {r["asset_id"] for r in rows} == {r["asset_id"] for r in payload["items"]}
    assert next(r for r in rows if r["title"])["title"] == "'=1+1"
    assert "SECRET" not in json.dumps(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"source": "all"},
        {"run_id": 0},
        {"kind": "bogus"},
        {"page_size": 501},
        {"page": 0},
        {"sort": "password"},
    ],
)
def test_invalid_filters_are_rejected(overrides):
    with pytest.raises(ValidationError):
        AssetQuery(**({"source": "scan", "run_id": 1} | overrides))


def test_malformed_legacy_urls_and_metadata_keep_unknown_without_crashing(asset_scan):
    from app.models.scan_run import ScanAsset

    with session_scope() as session:
        row = session.get(ScanAsset, asset_scan[1])
        row.url = "https://[broken?password=HIDDEN"
        row.status_code = 0
        row.attributes = {"content_type": {"unexpected": "SECRET"}}
        row.asset_type = "future_kind"
    with session_scope() as session:
        result = AssetCatalogService().list(
            session, AssetQuery(source="scan", run_id=asset_scan[0])
        )
    item = next(r for r in result.items if r.record_id == asset_scan[1])
    assert item.kind == "other"
    assert item.observation == "unknown"
    assert item.site is None
    assert item.content_type is None
    assert "HIDDEN" not in result.model_dump_json()


def test_queries_and_exports_do_not_modify_source_records(asset_scan):
    from sqlalchemy import event

    with session_scope() as session:
        writes = []

        def on_flush(*args):
            writes.append(True)

        event.listen(session, "before_flush", on_flush)
        query = AssetQuery(source="scan", run_id=asset_scan[0])
        AssetCatalogService().list(session, query)
        export_assets(session, query, "json")
        export_assets(session, query, "csv")
        assert not session.new and not session.dirty and not session.deleted
        assert writes == []


def test_uppercase_urls_in_titles_and_terminal_controls_are_not_exposed(asset_scan):
    from app.models.scan_run import ScanAsset

    with session_scope() as session:
        row = session.get(ScanAsset, asset_scan[1])
        row.title = "HTTPS://name:shortpass@site.test/a?code=SHORT_SECRET#PRIVATE_FRAGMENT"
        row.url = "https://site.test/a\x1b[31mRED\x1b[0m"
    with session_scope() as session:
        result = AssetCatalogService().list(
            session, AssetQuery(source="scan", run_id=asset_scan[0])
        )
        search = AssetCatalogService().list(
            session, AssetQuery(source="scan", run_id=asset_scan[0], q="SHORT_SECRET")
        )
    text = result.model_dump_json()
    assert "SHORT_SECRET" not in text
    assert "PRIVATE_FRAGMENT" not in text
    assert "shortpass" not in text
    assert search.matched == 0
    assert "\x1b" not in "".join(row.url for row in result.items)
