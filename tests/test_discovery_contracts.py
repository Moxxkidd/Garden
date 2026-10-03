import pytest
from pydantic import ValidationError

from app.schemas.scan import DiscoveredAsset, ScanOptions


def test_discovery_options_are_explicit_and_bounded():
    options = ScanOptions()
    assert options.collection_mode == "http"
    assert options.sitemap_enabled is False
    assert options.js_enabled is False
    assert options.max_candidates == 2000
    for invalid in [
        {"max_candidates": 10001},
        {"render_wait_ms": 5001},
        {"max_browser_requests": 2001},
        {"seed_input": "x" * (1024 * 1024 + 1)},
    ]:
        with pytest.raises(ValidationError):
            ScanOptions(**invalid)


def test_declared_candidates_keep_method_source_and_no_visit():
    item = DiscoveredAsset(
        url="https://site.test/upload",
        asset_type="endpoint",
        method="POST",
        source_kind="form_action",
        source_url="https://site.test/",
        auto_visit=False,
    )
    assert item.method == "POST" and not item.auto_visit
    assert item.source_kind == "form_action"


def test_ledger_retains_sources_and_separates_declared_methods():
    from app.services.discovery import DiscoveryLedger

    ledger = DiscoveryLedger("https://site.test/", 3)
    for page in ["https://site.test/a?token=SECRET", "https://site.test/b"]:
        ledger.add(
            DiscoveredAsset(
                url="https://site.test/api?q=SECRET",
                asset_type="endpoint",
                source_kind="html_link",
                source_url=page,
            )
        )
    ledger.add(
        DiscoveredAsset(
            url="https://site.test/api?q=SECRET",
            asset_type="endpoint",
            method="POST",
            source_kind="form_action",
            auto_visit=False,
        )
    )
    ledger.observed("https://site.test/api?q=SECRET", "GET")
    snapshot = ledger.snapshot(complete=False)
    assert len(snapshot["candidates"]) == 1
    assert snapshot["candidates"][0]["method"] == "POST"
    assert len(snapshot["observations"][0]["sources"]) == 2
    assert "SECRET" not in str(snapshot)
    assert snapshot["complete"] is False
    assert snapshot["stats"]["html_link"]["new_candidates"] == 1
    assert snapshot["stats"]["html_link"]["duplicate_candidates"] == 1


def test_ledger_enforces_candidate_limit_and_outside_scope():
    from app.services.discovery import DiscoveryLedger

    ledger = DiscoveryLedger("https://site.test/", 1)
    assert ledger.add(DiscoveredAsset(url="https://outside.test/a", asset_type="page")) is False
    assert ledger.add(DiscoveredAsset(url="https://site.test/a", asset_type="page")) is False
    snapshot = ledger.snapshot(complete=True)
    assert snapshot["truncated"] is True
    assert snapshot["candidates"][0]["reason"] == "outside_scope"


def test_new_metadata_columns_are_nullable_and_legacy_safe(app):
    from sqlalchemy import inspect

    from app.db.bootstrap import session_scope

    with session_scope() as session:
        schema = inspect(session.get_bind())
        for table, name in [
            ("scan_runs", "discovery_input_ref"),
            ("inventory_pages", "discovery_metadata"),
            ("inventory_endpoints", "discovery_metadata"),
        ]:
            columns = {c["name"]: c for c in schema.get_columns(table)}
            assert name in columns and columns[name]["nullable"]


def test_later_browser_budget_reason_replaces_pending_candidate():
    from app.services.discovery import DiscoveryLedger

    ledger = DiscoveryLedger("https://site.test/", 10)
    ledger.add(DiscoveredAsset(url="https://site.test/next", asset_type="page"))
    ledger.add(
        DiscoveredAsset(
            url="https://site.test/next",
            asset_type="page",
            source_kind="browser_navigation",
            auto_visit=False,
            skipped_reason="max_pages",
        )
    )
    assert ledger.snapshot(complete=False)["candidates"][0]["reason"] == "max_pages"


def test_truncated_source_count_is_unknown_in_public_catalog():
    from app.services.asset_catalog import safe_discovery
    from app.services.discovery import DiscoveryLedger

    ledger = DiscoveryLedger("https://site.test/", 5)
    for n in list(range(21)) + [20, 20]:
        ledger.add(
            DiscoveredAsset(
                url="https://site.test/asset",
                asset_type="page",
                source_url=f"https://site.test/source/{n}",
            )
        )
    item = ledger.snapshot(complete=False)["candidates"][0]
    assert item["source_count"] is None
    assert safe_discovery({"version": 1, **item})["source_count"] is None
