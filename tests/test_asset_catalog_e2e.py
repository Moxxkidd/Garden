"""Real browser selection, filtering, paging and full downloads using isolated records."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.test_scan_preview_e2e import _serve

pytest_plugins = ["tests.helpers.asset_catalog"]


@pytest.mark.parametrize("javascript_enabled", [False, True])
def test_asset_filters_survive_refresh_and_download_all_matches(
    app, asset_scan, asset_inventory, tmp_path, javascript_enabled
):
    with _serve(app) as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chromium")
        try:
            context = browser.new_context(
                java_script_enabled=javascript_enabled, viewport={"width": 390, "height": 844}
            )
            attempted_targets = []

            def guard(route):
                if route.request.url.startswith(origin + "/"):
                    route.continue_()
                else:
                    if "site.test" in route.request.url or "localhost:8080" in route.request.url:
                        attempted_targets.append(route.request.url)
                    route.abort()

            context.route("**/*", guard)
            page = context.new_page()
            page.goto(origin + "/assets")
            page.get_by_label("任务 ID", exact=True).fill(str(asset_scan[0]))
            page.get_by_role("button", name="查看清单", exact=True).click()
            expect(
                page.get_by_role("heading", name=f"scan #{asset_scan[0]} · 匹配 5 / 总记录 5")
            ).to_be_visible()
            page.get_by_label("类型", exact=True).select_option("page")
            page.get_by_label("每页记录", exact=True).fill("1")
            page.get_by_role("button", name="应用筛选", exact=True).click()
            expect(
                page.get_by_role("heading", name=f"scan #{asset_scan[0]} · 匹配 2 / 总记录 5")
            ).to_be_visible()
            assert parse_qs(urlsplit(page.url).query)["kind"] == ["page"]
            page.reload()
            expect(page.get_by_label("类型", exact=True)).to_have_value("page")
            first = page.locator("tbody tr").inner_text()
            page.get_by_role("link", name="下一页", exact=True).click()
            expect(page.get_by_text("第 2 / 2 页 · 本页 1 条", exact=True)).to_be_visible()
            assert page.locator("tbody tr").inner_text() != first
            page.get_by_text("查看来源", exact=True).click()
            expect(page.get_by_role("link", name="查看原任务与证据")).to_be_visible()
            with page.expect_download() as download_info:
                page.get_by_role("link", name="下载 JSON", exact=True).click()
            destination = tmp_path / "assets.json"
            download_info.value.save_as(destination)
            exported = json.loads(destination.read_text())
            assert exported["exported_count"] == 2
            assert all(row["kind"] == "page" for row in exported["items"])
            assert "SECRET" not in destination.read_text()
            page.goto(f"{origin}/assets?source=inventory&run_id={asset_inventory}")
            expect(
                page.get_by_role(
                    "heading", name=f"inventory #{asset_inventory} · 匹配 2 / 总记录 2"
                )
            ).to_be_visible()
            assert attempted_targets == []
        finally:
            browser.close()


@pytest.mark.parametrize("javascript_enabled", [False, True])
def test_grouped_view_preserves_filters_details_and_downloads(
    app, asset_scan, tmp_path, javascript_enabled
):
    with _serve(app) as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chromium")
        try:
            page = browser.new_page(
                java_script_enabled=javascript_enabled, viewport={"width": 390, "height": 844}
            )
            page.route(
                "**/*",
                lambda route: (
                    route.continue_()
                    if route.request.url.startswith(origin + "/")
                    else route.abort()
                ),
            )
            page.goto(f"{origin}/assets?source=scan&run_id={asset_scan[0]}")
            page.get_by_label("清单视图", exact=True).select_option("grouped")
            page.get_by_label("类型", exact=True).select_option("page")
            page.get_by_role("button", name="应用筛选", exact=True).click()
            expect(
                page.get_by_role("heading", name=f"scan #{asset_scan[0]} · 匹配 1 / 总归并资产 4")
            ).to_be_visible()
            assert parse_qs(urlsplit(page.url).query)["view"] == ["grouped"]
            page.reload()
            expect(page.get_by_label("清单视图", exact=True)).to_have_value("grouped")
            page.get_by_text("展开归并详情", exact=True).click()
            expect(page.get_by_text("原始观察与证据", exact=True)).to_be_visible()
            assert page.get_by_role("link", name="查看原任务与证据").count() == 2
            expect(page.get_by_text("变体数：未知", exact=True)).to_be_visible()
            with page.expect_download() as event:
                page.get_by_role("link", name="下载 JSON", exact=True).click()
            path = tmp_path / "grouped.json"
            event.value.save_as(path)
            data = json.loads(path.read_text())
            assert data["exported_count"] == 1
            assert data["items"][0]["observation_count"] == 2
            assert len(data["items"][0]["observations"]) == 2
            page.get_by_label("类型", exact=True).select_option("")
            page.get_by_label("每页记录", exact=True).fill("1")
            page.get_by_role("button", name="应用筛选", exact=True).click()
            page.get_by_role("link", name="下一页", exact=True).click()
            expect(page.get_by_text("第 2 / 4 页 · 本页 1 条", exact=True)).to_be_visible()
            assert parse_qs(urlsplit(page.url).query)["view"] == ["grouped"]
        finally:
            browser.close()
