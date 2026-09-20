"""Real local scans followed by read-only comparison, with and without JavaScript."""

import time

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from playwright.sync_api import expect, sync_playwright
from test_scan_preview_e2e import _serve

from app.schemas.assessment import AssessmentStartRequest
from app.schemas.scan import ScanOptions
from app.services.scan_reporting import ScanReportService


@pytest.mark.parametrize("javascript_enabled", [False, True])
def test_browser_compares_real_reruns_and_follows_observation_evidence(
    app, tmp_path, javascript_enabled
):
    target = FastAPI()
    state = {"phase": "before", "requests": 0}

    @target.get("/{path:path}")
    def page_response(path: str):
        state["requests"] += 1
        headers = {"Content-Security-Policy": "default-src 'self'"}
        missing = path == "shared" or path == ("old" if state["phase"] == "before" else "new")
        if not missing:
            headers["X-Content-Type-Options"] = "nosniff"
        links = (
            '<a href="/shared">shared</a><a href="/old">old</a><a href="/new">new</a>'
            if not path
            else ""
        )
        return HTMLResponse(
            f"<html><title>Local comparison</title><body>{links}</body></html>", headers=headers
        )

    def finished(service, run_id):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            run = service.get_scan(run_id)
            if run.status not in {"queued", "running"}:
                assert run.status == "completed", (run.error_code, run.error_message)
                return run
            time.sleep(0.05)
        pytest.fail("local scan did not finish")

    with _serve(target) as target_origin, _serve(app) as garden_origin:
        service = app.state.scan_service
        service.pipeline.report_service = ScanReportService(tmp_path / "reports")
        options = ScanOptions(
            max_pages=10,
            max_depth=1,
            max_resources=0,
            request_timeout_seconds=3,
            overall_timeout_seconds=60,
            retry_attempts=0,
        )
        source = finished(service, service.start_scan(target_origin, options).id)
        state["phase"] = "after"
        current = finished(
            service,
            service.start_assessment(
                AssessmentStartRequest(
                    url=target_origin,
                    rerun_of_run_id=source.id,
                    options=options,
                )
            ).id,
        )
        request_count = state["requests"]
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
                page.goto(f"{garden_origin}/scans/{current.id}")
                page.get_by_role("link", name="与上次对比", exact=True).click()
                expect(page.get_by_role("heading", name="可比性：配置与范围一致")).to_be_visible()
                expect(
                    page.get_by_text(
                        "新增观察 1 · 持续观察 1 · 未再观察到 1 · 无法判断 0", exact=True
                    )
                ).to_be_visible()
                link = page.get_by_role("link", name="上次观察及证据", exact=False).first
                anchor = link.get_attribute("href")
                link.click()
                expect(page.locator(anchor)).to_be_visible()
                expect(page.locator(anchor)).to_contain_text("HTTP 200")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                assert state["requests"] == request_count
                assert len(service.list_scans()) == 2
            finally:
                browser.close()
