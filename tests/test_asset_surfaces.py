import csv
import io
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli.main import app as cli
from app.db.bootstrap import session_scope
from app.models.scan_run import ScanAsset

pytest_plugins = ["tests.helpers.asset_catalog"]
NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


def test_asset_api_web_cli_and_export_use_same_filter(app, asset_scan):
    run_id = asset_scan[0]
    query = f"source=scan&run_id={run_id}&kind=page&page_size=1"
    with TestClient(app) as client:
        response = client.get(f"/api/assets?{query}")
        assert response.status_code == 200
        payload = response.json()
        html = client.get(f"/assets?{query}").text
        exported = client.get(f"/api/assets/export?{query}&format=json")
        csv_response = client.get(f"/api/assets/export?{query}&format=csv")
    result = CliRunner().invoke(
        cli,
        [
            "assets",
            "list",
            "--source",
            "scan",
            "--run-id",
            str(run_id),
            "--kind",
            "page",
            "--page-size",
            "1",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["items"] == payload["items"]
    assert payload["total"] == 5 and payload["matched"] == 2 and len(payload["items"]) == 1
    assert "匹配 2 / 总记录 5" in html
    assert payload["items"][0]["asset_id"] in html
    assert "page=2" in html and "kind=page" in html
    assert exported.json()["exported_count"] == 2
    assert len(list(csv.DictReader(io.StringIO(csv_response.text)))) == 2
    assert exported.headers["cache-control"] == "no-store"
    assert "attachment" in exported.headers["content-disposition"]
    assert "QUERY_SECRET" not in html + exported.text + csv_response.text + result.stdout


def test_asset_sources_are_reachable_from_existing_pages(app, asset_scan, asset_inventory):
    with TestClient(app) as client:
        scan = client.get(f"/scans/{asset_scan[0]}")
        inventory = client.get(f"/inventory/{asset_inventory}")
        landing = client.get("/assets")
        page = client.get(f"/api/assets?source=inventory&run_id={asset_inventory}")
    assert "统一资产清单" in scan.text
    assert f"/assets?source=scan&amp;run_id={asset_scan[0]}" in scan.text
    assert f"/assets?source=inventory&amp;run_id={asset_inventory}" in inventory.text
    assert landing.status_code == 200
    assert "选择来源任务" in landing.text
    assert page.json()["total"] == 2


def test_invalid_and_missing_scopes_never_fall_back_to_all_assets(app, asset_scan):
    with TestClient(app) as client:
        for query in [
            "",
            "source=scan",
            "source=all&run_id=1",
            "source=scan&run_id=0",
            f"source=scan&run_id={asset_scan[0]}&kind=unknown-type",
            f"source=scan&run_id={asset_scan[0]}&page_size=999",
        ]:
            assert client.get(f"/api/assets?{query}").status_code in {400, 422}
        assert client.get("/api/assets?source=scan&run_id=99999").status_code == 404
        response = client.get(f"/api/assets?source=scan&run_id={asset_scan[0]}&q=notfound")
        assert response.status_code == 200 and response.json()["matched"] == 0


def test_cli_export_all_pages_and_refuses_to_overwrite(asset_scan, tmp_path):
    output = tmp_path / "assets.json"
    args = [
        "assets",
        "export",
        "--source",
        "scan",
        "--run-id",
        str(asset_scan[0]),
        "--format",
        "json",
        "--output",
        str(output),
    ]
    runner = CliRunner()
    first = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output
    assert len(json.loads(output.read_text())["items"]) == 5
    original = output.read_bytes()
    assert runner.invoke(cli, args).exit_code != 0
    assert output.read_bytes() == original


def test_large_export_and_context_filter_do_not_truncate_or_reorder(app, asset_scan):
    with session_scope() as session:
        for n in range(205):
            session.add(
                ScanAsset(
                    scan_run_id=asset_scan[0],
                    asset_type="page",
                    url=f"https://site.test/b/{n:03}",
                    status_code=200,
                    discovered_at=NOW,
                )
            )
    query = f"source=scan&run_id={asset_scan[0]}&kind=page&context=unknown&sort=url&order=desc"
    with TestClient(app) as client:
        first = client.get(f"/api/assets?{query}&page_size=200").json()
        second = client.get(f"/api/assets?{query}&page_size=200&page=2").json()
        export = client.get(f"/api/assets/export?{query}&format=json").json()
    assert first["matched"] == second["matched"] == export["exported_count"] == 205
    assert first["items"] + second["items"] == export["items"]
    assert export["items"][0]["url"].endswith("/204")


def test_web_escapes_titles_and_empty_page_keeps_scope(app, asset_scan):
    with session_scope() as session:
        session.get(ScanAsset, asset_scan[1]).title = "<script>alert(1)</script>"
    with TestClient(app) as client:
        html = client.get(f"/assets?source=scan&run_id={asset_scan[0]}").text
        empty = client.get(f"/assets?source=scan&run_id={asset_scan[0]}&q=nomatch").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "没有匹配记录" in empty
    assert "不代表" in empty
