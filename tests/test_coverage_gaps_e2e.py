"""The recorded gap explains a bounded rerun without submitting it early."""

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from app.schemas.scan import ScanOptions
from tests.test_scan_preview_e2e import _serve
from tests.test_url_scan_pipeline import _service


@pytest.mark.parametrize("javascript_enabled", [False, True])
def test_browser_gap_samples_and_reuse_preview(app, tmp_path, javascript_enabled):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/next?token=sample-secret">Next</a>',
        )

    service = _service(tmp_path, handler)
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(max_pages=1))
    with _serve(app) as garden_origin:
        app.state.scan_service = service
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chromium")
            try:
                context = browser.new_context(
                    java_script_enabled=javascript_enabled, viewport={"width": 390, "height": 844}
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
                page.goto(f"{garden_origin}/scans/{scan.id}")
                section = page.locator("#coverage-gaps")
                expect(section).to_contain_text("匿名：页面数上限（1）；未请求 URL：1。")
                expect(page.locator("#result-summary")).to_contain_text(
                    "100%（执行进度，不代表覆盖完整）"
                )
                section.get_by_text("查看脱敏样例（最多 3 条）", exact=True).click()
                expect(
                    section.get_by_text("http://127.0.0.1/next?token=[REDACTED]", exact=True)
                ).to_be_visible()
                assert "sample-secret" not in section.inner_text()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.get_by_role("link", name="沿用配置重新扫描", exact=True).click()
                expect(page.locator("#scan-max_pages")).to_have_value("1")
                page.get_by_text("预算选项", exact=True).click()
                page.locator("#scan-max_pages").fill("2")
                page.get_by_role("button", name="预览扫描", exact=True).click()
                expect(page.get_by_role("button", name="开始匿名扫描", exact=True)).to_be_visible()
                assert calls == ["http://127.0.0.1/"]
                assert len(service.list_scans()) == 1
            finally:
                browser.close()
