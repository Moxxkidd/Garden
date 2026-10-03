from fastapi.testclient import TestClient

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanAsset, ScanRun

pytest_plugins = ["tests.helpers.asset_catalog"]


def test_provenance_filter_and_v2_candidates_share_catalog_export(app, asset_scan):
    with session_scope() as session:
        run = session.get(ScanRun, asset_scan[0])
        run.asset_metadata = {
            "version": 2,
            "complete": False,
            "stats": {"html_link": {"discovered": 2}},
            "candidates": [
                {
                    "url": "https://site.test/submit?token=SECRET",
                    "asset_type": "endpoint",
                    "method": "POST",
                    "reason": "declaration_only",
                    "source_kind": "form_action",
                    "sources": [{"kind": "form_action", "url": "https://site.test/"}],
                }
            ],
        }
        asset = session.get(ScanAsset, asset_scan[1])
        asset.attributes = {
            "discovery": {
                "version": 1,
                "sources": [{"kind": "html_link", "url": "https://site.test/from?key=SECRET"}],
                "source_count": 1,
            }
        }
    with TestClient(app) as client:
        query = f"source=scan&run_id={asset_scan[0]}&source_kind=html_link"
        data = client.get("/api/assets?" + query).json()
        assert data["matched"] == 1
        assert data["items"][0]["discovery"]["sources"][0]["kind"] == "html_link"
        assert "SECRET" not in str(data)
        candidate = client.get(
            f"/api/assets?source=scan&run_id={asset_scan[0]}&view=candidates"
        ).json()
        assert candidate["candidate_count"] == 1
        assert candidate["items"][0]["method"] == "POST"
        assert candidate["items"][0]["candidate_reason"] == "declaration_only"
        exported = client.get("/api/assets/export?" + query).json()
        assert exported["items"] == data["items"]
        assert "页面链接" in client.get("/assets?" + query).text


def test_inventory_preserves_new_source_metadata(asset_inventory):
    from datetime import datetime, timezone

    from app.integrations.inventory.playwright_gateway import ObservedPage
    from app.models.inventory_run import InventoryRun
    from app.services.inventory import InventoryBuildService

    with session_scope() as session:
        run = session.get(InventoryRun, asset_inventory)
        page = ObservedPage(
            url="https://site.test/new",
            title="new",
            visited_at=datetime.now(timezone.utc),
            depth=1,
            discovered_urls=[],
            discovery_metadata={
                "version": 1,
                "sources": [
                    {"kind": "browser_navigation", "url": "https://site.test/?token=SECRET"}
                ],
            },
        )
        stored = InventoryBuildService()._upsert_page(session, run, page)
        assert stored.discovery_metadata["sources"][0]["kind"] == "browser_navigation"
        assert "SECRET" not in str(stored.discovery_metadata)
