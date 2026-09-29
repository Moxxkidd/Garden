"""Passive validity hints must never turn a response into proof of a business asset."""

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from app.cli.main import app as cli
from app.db.bootstrap import session_scope
from app.models.scan_run import ScanAsset, ScanRun
from app.schemas.assets import AssetQuery
from app.services.asset_catalog import AssetCatalogService

pytest_plugins = ["tests.helpers.asset_catalog"]


def test_legacy_assets_remain_unconfirmed_and_candidates_unknown(app, asset_scan):
    with TestClient(app) as client:
        data = client.get(f"/api/assets?source=scan&run_id={asset_scan[0]}").json()
    assert data["validity_rule_version"] == "passive-v1"
    assert data["candidate_count"] is None
    assert data["validity_counts"]["response_observed"] == 4
    assert all(r["validity"]["business_validity"] == "unconfirmed" for r in data["items"])
    assert (
        next(r for r in data["items"] if r["status_codes"] == [403])["validity"]["access"]
        == "restricted"
    )


def test_passive_signals_keep_origin_context_and_response_differences(asset_scan):
    from app.services.asset_validity import capture_traits

    with session_scope() as session:
        old = session.get(ScanAsset, asset_scan[1])
        for path, status, body in [
            ("/not-there", 404, "<html><title>404 Not Found</title></html>"),
            ("/fake", 200, "<html><title>404 Not Found</title></html>"),
            ("/one", 500, "same error"),
            ("/two", 500, "same error"),
            ("/three", 500, "same error"),
        ]:
            session.add(
                ScanAsset(
                    scan_run_id=old.scan_run_id,
                    context_id=old.context_id,
                    asset_type="page",
                    url="https://site.test" + path,
                    method="GET",
                    status_code=status,
                    discovered_at=old.discovered_at,
                    attributes={"validity_traits": capture_traits(body, "text/html")},
                )
            )
        session.flush()
        result = AssetCatalogService().select(
            session, AssetQuery(source="scan", run_id=asset_scan[0])
        )
        rows = {r.url: r for r in result.items}
        assert "suspected_soft_404" in [
            f.code for f in rows["https://site.test/fake"].validity.flags
        ]
        assert "uniform_error_response" in [
            f.code for f in rows["https://site.test/one"].validity.flags
        ]
        assert rows["https://site.test/not-there"].validity.business_validity == "unconfirmed"
        assert "SECRET" not in result.model_dump_json()
        assert "signature" not in result.model_dump_json()


def test_login_fallback_and_redirect_alias_require_evidence(asset_scan):
    from app.services.asset_validity import capture_traits

    with session_scope() as session:
        asset = session.get(ScanAsset, asset_scan[1])
        asset.attributes = {
            "validity_traits": capture_traits(
                '<html><title>Sign in</title><form><input type="password"></form></html>',
                "text/html",
            ),
            "catalog_redirects": [
                {
                    "requested_url": "https://site.test/admin?token=SECRET",
                    "final_url": "https://site.test/login?next=SECRET",
                }
            ],
        }
        result = AssetCatalogService().select(
            session, AssetQuery(source="scan", run_id=asset_scan[0])
        )
        row = next(r for r in result.items if r.record_id == asset_scan[1])
        assert {"login_page", "suspected_login_fallback", "redirect_alias"} <= {
            f.code for f in row.validity.flags
        }
        assert row.validity.aliases == ["https://site.test/admin?token=[REDACTED]"]
        assert "SECRET" not in row.model_dump_json()
    traits = capture_traits(
        '<html><title>Change password</title><input type="password"></html>', "text/html"
    )
    assert traits["login_page"] is False
    assert capture_traits("same prefix", "text/html", complete=False)["signature"] is None


def test_candidates_are_separate_and_all_surfaces_export_same_rows(app, asset_scan, tmp_path):
    with session_scope() as session:
        run = session.get(ScanRun, asset_scan[0])
        run.asset_metadata = {
            "version": 1,
            "candidates": [
                {
                    "url": "https://site.test/later?token=SECRET",
                    "asset_type": "page",
                    "source_url": "https://site.test/",
                }
            ],
        }
    query = f"source=scan&run_id={asset_scan[0]}&view=candidates"
    with TestClient(app) as client:
        data = client.get("/api/assets?" + query).json()
        assert data["total"] == 1 and data["candidate_count"] == 1
        assert data["validity_counts"]["response_observed"] == 4
        row = data["items"][0]
        assert row["validity"]["verification"] == "candidate" and row["status_codes"] == []
        assert row["observation"] == "unknown"
        html = client.get("/assets?" + query).text
        assert "候选" in html and "SECRET" not in html
        exported = client.get("/api/assets/export?" + query).json()
        assert exported["items"] == data["items"]
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
            "candidates",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["items"] == data["items"]


def test_repeated_responses_do_not_cross_sites_or_contexts(asset_scan):
    from app.services.asset_validity import capture_traits

    with session_scope() as session:
        original = session.get(ScanAsset, asset_scan[1])
        for i, site in enumerate(["one.test", "two.test", "three.test"]):
            session.add(
                ScanAsset(
                    scan_run_id=original.scan_run_id,
                    context_id=original.context_id,
                    asset_type="page",
                    url=f"https://{site}/{i}",
                    method="GET",
                    status_code=200,
                    discovered_at=original.discovered_at,
                    attributes={
                        "validity_traits": capture_traits("<html>common shell</html>", "text/html")
                    },
                )
            )
        session.flush()
        data = AssetCatalogService().select(
            session, AssetQuery(source="scan", run_id=asset_scan[0])
        )
    assert not any(f.code == "uniform_response" for r in data.items for f in r.validity.flags)


def test_identical_responses_do_not_combine_identity_contexts(asset_scan):
    from app.models.scan_context import ScanContext
    from app.services.asset_validity import capture_traits

    with session_scope() as session:
        original = session.get(ScanAsset, asset_scan[1])
        for i, kind in enumerate(["anonymous", "user", "admin"]):
            context = session.scalar(
                select(ScanContext).where(
                    ScanContext.scan_run_id == original.scan_run_id, ScanContext.kind == kind
                )
            )
            if context is None:
                context = ScanContext(scan_run_id=original.scan_run_id, kind=kind)
                session.add(context)
                session.flush()
            session.add(
                ScanAsset(
                    scan_run_id=original.scan_run_id,
                    context_id=context.id,
                    asset_type="page",
                    url=f"https://site.test/context-{i}",
                    method="GET",
                    status_code=200,
                    discovered_at=original.discovered_at,
                    attributes={"validity_traits": capture_traits("common shell", "text/html")},
                )
            )
        session.flush()
        data = AssetCatalogService().select(
            session, AssetQuery(source="scan", run_id=asset_scan[0])
        )
    assert not any(f.code == "uniform_response" for r in data.items for f in r.validity.flags)


def test_scan_collects_redirect_evidence_and_unrequested_candidates_without_probing(tmp_path):
    import httpx

    from app.schemas.scan import ScanOptions
    from tests.test_url_scan_pipeline import _service

    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/admin":
            return httpx.Response(302, headers={"location": "/login"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                '<title>Sign in</title><input type="password">'
                '<a href="/private?token=SECRET">Later</a>'
            ),
        )

    service = _service(tmp_path, handler)
    run = service.start_scan("http://127.0.0.1/admin", ScanOptions(max_pages=1))
    with session_scope() as session:
        result = AssetCatalogService().select(session, AssetQuery(source="scan", run_id=run.id))
        assert result.candidate_count == 1
        assert {"redirect_alias", "suspected_login_fallback"} <= {
            f.code for f in result.items[0].validity.flags
        }
        candidates = AssetCatalogService().select(
            session, AssetQuery(source="scan", run_id=run.id, view="candidates")
        )
        assert candidates.items[0].validity.verification == "candidate"
        assert "SECRET" not in candidates.model_dump_json()
    assert calls == ["/admin", "/login"]


def test_native_inventory_traits_survive_reload_without_using_previews(asset_inventory):
    from app.integrations.inventory.playwright_gateway import ObservedPage
    from app.models.inventory_run import InventoryRun
    from app.services.inventory import InventoryBuildService

    with session_scope() as session:
        run = session.get(InventoryRun, asset_inventory)
        service = InventoryBuildService()
        page = service._upsert_page(
            session,
            run,
            ObservedPage(
                url="http://localhost:8080/soft",
                title="404 Not Found",
                visited_at=datetime.now(timezone.utc),
                depth=0,
                discovered_urls=[],
                status_code=200,
                content_type="text/html",
                response_text="<title>404 Not Found</title>",
            ),
        )
        assert page.response_traits["not_found_title"] is True
    with session_scope() as session:
        result = AssetCatalogService().select(
            session, AssetQuery(source="inventory", run_id=asset_inventory)
        )
        row = next(r for r in result.items if r.url.endswith("/soft"))
        assert "suspected_soft_404" in {f.code for f in row.validity.flags}


def test_validity_filter_preserves_group_observations_and_export(app, asset_scan):
    from app.services.asset_validity import capture_traits

    with session_scope() as session:
        row = session.get(ScanAsset, asset_scan[1])
        row.attributes = {
            "validity_traits": capture_traits("<title>404 Not Found</title>", "text/html")
        }
    query = f"source=scan&run_id={asset_scan[0]}&view=grouped&validity=suspected_soft_404"
    with TestClient(app) as client:
        data = client.get("/api/assets?" + query).json()
        assert data["matched"] == 1
        assert data["items"][0]["observation_count"] == 1
        assert data["items"][0]["validity_flags"][0]["code"] == "suspected_soft_404"
        html = client.get("/assets?" + query).text
        assert "疑似软 404" in html
        assert "validity=suspected_soft_404" in html
        assert client.get("/api/assets/export?" + query).json()["items"] == data["items"]
        assert (
            client.get(
                "/api/assets?" + query.replace("suspected_soft_404", "confirmed")
            ).status_code
            == 400
        )


def test_truncated_request_snapshots_cannot_trigger_uniform_response(tmp_path):
    from app.db.bootstrap import get_session
    from app.services.context_collection import ContextCollectionService, ObservedRequest
    from app.services.session_storage import SessionStorageService
    from tests.helpers.assessment import make_authenticated_run

    class Gateway:
        def collect(self, run, context):
            return [], [
                ObservedRequest(
                    method="GET",
                    url=f"https://site.test/{i}",
                    headers={},
                    body=None,
                    status_code=200,
                    response_headers={"content-type": "text/html"},
                    response_text='<title>Sign in</title><input type="password">',
                    response_complete=False,
                )
                for i in range(3)
            ]

    with get_session() as session:
        run = make_authenticated_run(session)
        user = next(c for c in run.contexts if c.kind == "user")
        ContextCollectionService(
            gateway=Gateway(), storage_service=SessionStorageService(tmp_path / "captures")
        ).collect(session, run, user)
        rows = AssetCatalogService().select(session, AssetQuery(source="scan", run_id=run.id)).items
        assert all(not r.validity.flags for r in rows)
        assert all(r.validity.assessment == "insufficient_evidence" for r in rows)


def test_cli_distinguishes_insufficient_material(asset_scan, monkeypatch):
    from rich.console import Console

    import app.cli.assets as assets_cli

    monkeypatch.setattr(assets_cli, "console", Console(width=400, no_color=True))
    result = CliRunner().invoke(
        cli, ["assets", "list", "--source", "scan", "--run-id", str(asset_scan[0])]
    )
    assert result.exit_code == 0
    assert "判断材料不足" in result.stdout


def test_persisted_response_evidence_counts_as_observed_even_without_asset_status(asset_scan):
    from app.models.scan_run import ScanEvidence

    with session_scope() as session:
        asset = session.get(ScanAsset, asset_scan[1])
        asset.status_code = None
        session.add(
            ScanEvidence(
                scan_run_id=asset.scan_run_id,
                asset_id=asset.id,
                evidence_type="http",
                title="response",
                source_url=asset.url,
                summary="stored response",
                data={"status_code": 403},
                collected_at=asset.discovered_at,
            )
        )
        session.flush()
        result = AssetCatalogService().select(
            session,
            AssetQuery(source="scan", run_id=asset_scan[0], observation="response_observed"),
        )
    row = next(r for r in result.items if r.record_id == asset_scan[1])
    assert row.status_codes == [403]
    assert row.validity.verification == "response_observed"
    assert row.validity.access == "restricted"
    assert result.validity_counts["response_observed"] == 4
