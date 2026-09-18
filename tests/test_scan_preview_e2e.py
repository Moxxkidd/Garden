"""Local browser coverage for native anonymous preview forms."""

import socket
import threading
import time
from contextlib import contextmanager

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from playwright.sync_api import expect, sync_playwright

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanRun
from app.services.scan_reporting import ScanReportService


@contextmanager
def _serve(application):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(application, log_level="warning", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started, "local fixture server did not start"
            yield origin
        finally:
            server.should_exit = True
            thread.join(timeout=5)


@pytest.mark.parametrize("javascript_enabled", [False, True])
def test_browser_preview_edit_confirm_preserves_budget_without_early_requests(
    app, tmp_path, javascript_enabled
):
    target = FastAPI()
    requests = []

    @target.get("/", response_class=HTMLResponse)
    def target_page():
        requests.append("entry")
        return "<html><title>Local preview target</title><body>Authorized fixture</body></html>"

    with _serve(target) as target_origin, _serve(app) as garden_origin:
        app.state.scan_service.pipeline.report_service = ScanReportService(tmp_path / "reports")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chromium")
            try:
                context = browser.new_context(
                    java_script_enabled=javascript_enabled, viewport={"width": 390, "height": 844}
                )
                # These tests never fetch CDN assets or any non-local destination.
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
                page.get_by_label("Entry URL").fill(target_origin + "/?query=" + "x" * 80)
                page.get_by_text("预算选项", exact=True).click()
                page.locator("#scan-max_pages").fill("7")
                page.locator("#scan-max_depth").fill("1")
                page.locator("#scan-retry_attempts").fill("0")
                page.get_by_role("button", name="预览扫描", exact=True).click()
                expect(page.get_by_text("登录身份：不使用（仅匿名）", exact=False)).to_be_visible()
                assert requests == []
                assert app.state.scan_service.list_scans() == []
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                expect(page.locator('form[action="/scans/confirm"]')).to_have_attribute(
                    "hx-boost", "false"
                )
                page.get_by_role("button", name="返回修改", exact=True).click()
                expect(page.locator("#scan-max_pages")).to_have_value("7")
                expect(page.locator("#scan-max_depth")).to_have_value("1")
                page.get_by_text("预算选项", exact=True).click()
                page.locator("#scan-max_pages").fill("8")
                page.get_by_role("button", name="预览扫描", exact=True).click()
                assert requests == []
                # A stale configuration must produce a visible 409 without starting work.
                app.state.scan_service.settings.scan_request_timeout_seconds = 8
                with page.expect_response(
                    lambda response: response.url.endswith("/scans/confirm")
                ) as stale:
                    page.get_by_role("button", name="开始匿名扫描", exact=True).click()
                assert stale.value.status == 409
                expect(page.get_by_role("alert")).to_contain_text("预览已过期")
                assert requests == []
                page.get_by_role("button", name="预览扫描", exact=True).click()
                page.get_by_role("button", name="开始匿名扫描", exact=True).click()
                page.wait_for_url("**/scans/*")
                run_id = int(page.url.rsplit("/", 1)[1])
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    run = app.state.scan_service.get_scan(run_id)
                    if run.status in {"completed", "completed_with_warnings", "failed"}:
                        break
                    time.sleep(0.05)
                assert run.status == "completed", (run.error_code, run.error_message)
                assert requests
                with session_scope() as session:
                    stored = session.get(ScanRun, run_id)
                    assert stored.options["max_pages"] == 8
                    assert stored.options["max_depth"] == 1
                    assert stored.options["retry_attempts"] == 0
                # Reuse the completed run through the result page, with no new target
                # requests until the second confirmation (also works without JavaScript).
                original = app.state.scan_service.get_reuse_configuration(run_id)
                request_count = len(requests)
                page.reload()
                page.get_by_role("link", name="沿用配置重新扫描", exact=True).click()
                expect(page.locator("#scan-max_pages")).to_have_value("8")
                expect(page.locator("#scan-retry_attempts")).to_have_value("0")
                page.get_by_text("预算选项", exact=True).click()
                page.locator("#scan-max_pages").fill("9")
                page.get_by_role("button", name="预览扫描", exact=True).click()
                expect(page.get_by_text(f"沿用任务 #{run_id}", exact=False)).to_be_visible()
                page.get_by_role("button", name="返回修改", exact=True).click()
                expect(page.locator("#scan-max_pages")).to_have_value("9")
                page.get_by_role("button", name="预览扫描", exact=True).click()
                assert len(requests) == request_count
                assert len(app.state.scan_service.list_scans()) == 1
                page.get_by_role("button", name="开始匿名扫描", exact=True).click()
                page.wait_for_url("**/scans/*")
                new_id = int(page.url.rsplit("/", 1)[1])
                assert new_id != run_id
                expect(page.get_by_role("link", name=f"任务 #{run_id}", exact=True)).to_be_visible()
                assert app.state.scan_service.get_scan(new_id).rerun_of_run_id == run_id
                assert app.state.scan_service.get_reuse_configuration(run_id) == original
                # Force server validation beyond the browser's numeric constraint.
                page.goto(garden_origin)
                page.get_by_label("Entry URL").fill(target_origin)
                page.get_by_text("预算选项", exact=True).click()
                page.locator("#scan-max_pages").fill("999")
                page.locator('form[action="/scans/preview"]').evaluate(
                    "form => form.noValidate = true"
                )
                expect(page.locator('form[action="/scans/preview"]')).to_have_attribute(
                    "hx-boost", "false"
                )
                with page.expect_response(
                    lambda response: response.url.endswith("/scans/preview")
                ) as invalid:
                    page.get_by_role("button", name="预览扫描", exact=True).click()
                assert invalid.value.status == 422
                expect(page.locator("#error-max_pages")).to_be_visible()
                expect(page.locator("#scan-max_pages")).to_be_focused()
                assert len(app.state.scan_service.list_scans()) == 2
            finally:
                browser.close()
