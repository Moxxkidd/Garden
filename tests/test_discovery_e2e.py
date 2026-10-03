"""Real discovery submission and source-filtered catalog in desktop/mobile browsers."""

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from playwright.sync_api import expect, sync_playwright
from test_scan_preview_e2e import _serve

from app.services.scan_reporting import ScanReportService


@pytest.mark.parametrize("width,javascript", [(1280, True), (390, False)])
def test_discovery_form_catalog_and_report(app, tmp_path, width, javascript):
    target = FastAPI()
    paths = []

    @target.get("/{path:path}")
    def respond(path: str):
        paths.append(path)
        return HTMLResponse(
            '<a href="/next">Next</a><form action="/submit" method="POST"></form>'
            if not path
            else "Page"
        )

    with _serve(target) as target_origin, _serve(app) as garden_origin:
        app.state.scan_service.pipeline.report_service = ScanReportService(tmp_path / "reports")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chromium")
            try:
                context = browser.new_context(
                    java_script_enabled=javascript, viewport={"width": width, "height": 900}
                )
                context.route(
                    "**/*",
                    lambda route: (
                        route.continue_()
                        if route.request.url.startswith(garden_origin + "/")
                        else route.abort()
                    ),
                )
                page = context.new_page()
                page.goto(garden_origin)
                page.get_by_label("Entry URL").fill(target_origin)
                page.get_by_text("发现选项", exact=True).click()
                page.get_by_label("JS 线索", exact=True).select_option("true")
                page.locator('[name="render_wait_ms"]').fill("0")
                page.locator('[name="seed_input"]').fill(target_origin + "/seed?token=PRIVATE_DEMO")
                page.get_by_role("button", name="预览扫描", exact=True).click()
                assert "PRIVATE_DEMO" not in page.content()
                assert paths == []
                page.get_by_role("button", name="返回修改", exact=True).click()
                expect(page.locator('[name="render_wait_ms"]')).to_have_value("0")
                page.get_by_role("button", name="预览扫描", exact=True).click()
                page.get_by_role("button", name="开始匿名扫描", exact=True).click()
                page.wait_for_url("**/scans/*")
                run_id = int(page.url.rsplit("/", 1)[1])
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    run = app.state.scan_service.get_scan(run_id)
                    if run.status not in {"queued", "running"}:
                        break
                    time.sleep(0.05)
                assert run.status == "completed", (run.error_code, run.error_message)
                assert set(paths) == {"", "next", "seed"}
                page.reload()
                expect(page.get_by_role("heading", name="发现来源收益")).to_be_visible()
                page.goto(f"{garden_origin}/assets?source=scan&run_id={run_id}")
                page.get_by_label("发现来源", exact=True).select_option("url_import")
                page.get_by_role("button", name="应用筛选", exact=True).click()
                expect(page.get_by_label("发现来源", exact=True)).to_have_value("url_import")
                assert "PRIVATE_DEMO" not in page.content()
                exported = context.request.get(
                    f"{garden_origin}/api/assets/export?source=scan&run_id={run_id}&source_kind=url_import"
                ).json()
                assert len(exported["items"]) == 1
                assert exported["items"][0]["discovery"]["sources"][0]["kind"] == "url_import"
                assert "PRIVATE_DEMO" not in json.dumps(exported)
                report = (tmp_path / "reports" / f"scan-{run_id}.md").read_text()
                assert "发现来源收益" in report and "URL 导入" in report
                assert "PRIVATE_DEMO" not in report
                page.screenshot(path=str(tmp_path / f"discovery-{width}.png"), full_page=True)
            finally:
                browser.close()
