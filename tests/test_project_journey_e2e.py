"""Real local two-origin project journey, without JavaScript or external services."""

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from playwright.sync_api import expect, sync_playwright

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.target import TargetCreate
from app.services.scan_reporting import ScanReportService
from app.services.targets import TargetService
from tests.test_scan_preview_e2e import _serve


def test_two_origins_project_scan_history_and_download_without_js(app, seeded_records, tmp_path):
    site = FastAPI()

    @site.get("/", response_class=HTMLResponse)
    def home():
        return '<html><title>Project fixture</title><a href="/asset">Asset</a></html>'

    @site.get("/asset", response_class=HTMLResponse)
    def asset():
        return "<html><title>Asset fixture</title><p>Authorized local fixture</p></html>"

    with _serve(site) as first, _serve(site) as second, _serve(app) as garden:
        app.state.scan_service.pipeline.report_service = ScanReportService(tmp_path / "reports")
        with session_scope() as session:
            target = session.get(Target, seeded_records["target"].id)
            target.base_url = first
            other = TargetService().create(
                session,
                TargetCreate(
                    name="Second fixture",
                    base_url=second,
                    type="web",
                    owner="fixture",
                    tags=[],
                    status="active",
                ),
            )
            targets = [(target.id, first), (other.id, second)]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chromium")
            try:
                context = browser.new_context(
                    java_script_enabled=False, viewport={"width": 1280, "height": 900}
                )
                context.route(
                    "**/*",
                    lambda route: (
                        route.continue_()
                        if route.request.url.startswith(garden + "/")
                        else route.abort()
                    ),
                )
                page = context.new_page()
                page.set_default_timeout(8000)
                page.goto(garden + "/projects")
                page.get_by_label("项目名称", exact=True).fill("Local asset library")
                page.get_by_role("button", name="创建项目", exact=True).click()
                page.wait_for_url("**/projects/*")
                project_url = page.url
                reports = []
                for number, (tid, origin) in enumerate(targets, 1):
                    page.goto(project_url)
                    form = page.locator('form[action$="/entries"]')
                    form.get_by_label("Target", exact=True).select_option(str(tid))
                    form.get_by_label("入口名称", exact=True).fill(f"Entry {number}")
                    form.get_by_label("入口 URL", exact=True).fill(origin + "/")
                    form.get_by_role("button", name="添加入口", exact=True).click()
                    page.get_by_role("link", name="预览新扫描", exact=True).nth(number - 1).click()
                    page.locator('form[action="/scans/preview"] button[type="submit"]').click()
                    expect(
                        page.get_by_text("本次结果将关联到项目入口", exact=False)
                    ).to_be_visible()
                    page.get_by_role("button", name="开始匿名扫描", exact=True).click()
                    page.wait_for_url("**/scans/*")
                    run_id = int(page.url.rsplit("/", 1)[1])
                    run = app.state.scan_service.wait_for_completion(run_id, timeout_seconds=30)
                    assert run.status in {"completed", "completed_with_warnings"}
                    reports.append((Path(run.report_path), Path(run.report_path).read_bytes()))
                page.goto(project_url + "/assets")
                expect(page.get_by_text("4 个主体", exact=False)).to_be_visible()
                page.get_by_role("link", name=first + "/asset", exact=True).click()
                page.get_by_label("备注（最多 2000 字）", exact=True).fill("Reviewed local fixture")
                page.get_by_label("标签（每行一项，最多 20 项，每项 40 字）", exact=True).fill(
                    "reviewed"
                )
                page.get_by_role("button", name="保存备注", exact=True).click()
                expect(page.get_by_text("备注：Reviewed local fixture", exact=True)).to_be_visible()
                page.goto(project_url + "/assets")
                page.get_by_label("标签", exact=True).fill("reviewed")
                page.get_by_role("button", name="应用筛选", exact=True).click()
                expect(page.get_by_text("筛选匹配 1 条", exact=False)).to_be_visible()
                page.reload()
                expect(page.get_by_label("标签", exact=True)).to_have_value("reviewed")
                with page.expect_download() as download:
                    page.get_by_role("link", name="导出 JSON 全部筛选结果", exact=True).click()
                exported = json.loads(Path(download.value.path()).read_text())
                assert exported["exported_count"] == 1
                assert exported["items"][0]["note"] == "Reviewed local fixture"
                artifacts = Path("output/playwright/v07")
                artifacts.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(artifacts / "project-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(project_url + "/assets")
                expect(page.get_by_role("heading", name="项目资产清单", exact=True)).to_be_visible()
                page.screenshot(path=str(artifacts / "project-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                for path, content in reports:
                    assert path.read_bytes() == content
            except Exception:
                Path(".verification/journey-failure.html").write_text(page.content())
                Path("output/playwright/v07").mkdir(parents=True, exist_ok=True)
                page.screenshot(path="output/playwright/v07/journey-failure.png", full_page=True)
                raise
            finally:
                browser.close()
