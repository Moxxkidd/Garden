"""SSE and the single polling fallback preserve user state in a real browser."""

from datetime import datetime, timezone

import pytest
from playwright.sync_api import expect, sync_playwright

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanRun
from tests.helpers.project_assets import source_run
from tests.test_scan_preview_e2e import _serve


@pytest.mark.parametrize("width,fallback", [(1280, False), (390, True)])
def test_live_progress_preserves_page_state(app, seeded_records, tmp_path, width, fallback):
    with _serve(app) as origin:
        with session_scope() as session:
            run = source_run(session, seeded_records["target"])
            run.status = "queued"
            run.current_stage = "collect"
            run.progress = 8
            run.started_at = None
            run.options = {**run.options, "feedback_version": 1}
            run_id = run.id
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chromium")
            try:
                page = browser.new_page(viewport={"width": width, "height": 844})
                requests = []

                def route(r):
                    requests.append(r.request.url)
                    if not r.request.url.startswith(origin + "/") or (
                        fallback and r.request.url.endswith("/events")
                    ):
                        r.abort()
                    else:
                        r.continue_()

                page.route("**/*", route)
                page.goto(f"{origin}/scans/{run_id}")
                page.locator("#execution-details").evaluate("(el)=>el.open=false")
                page.locator("#coverage-gaps").scroll_into_view_if_needed()
                scroll = page.evaluate("window.scrollY")
                with session_scope() as session:
                    run = session.get(ScanRun, run_id)
                    run.progress = 32
                    run.status = "running"
                    run.started_at = datetime.now(timezone.utc)
                    run.asset_metadata = {
                        "version": 2,
                        "complete": False,
                        "truncated": False,
                        "stats": {
                            "entry": {
                                "request_attempts": 1,
                                "response_observed": 1,
                                "new_candidates": 3,
                            }
                        },
                        "pending_count": 2,
                    }
                expect(page.locator("[data-progress=activity]")).to_contain_text(
                    "收到 1 次响应", timeout=7000 if fallback else 2500
                )
                assert not page.locator("#execution-details").evaluate("(el)=>el.open")
                assert abs(page.evaluate("window.scrollY") - scroll) < 2
                assert page.evaluate('performance.getEntriesByType("navigation").length') == 1
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                expect(page.locator("[data-scan-started]")).to_contain_text("已用时")
                page.screenshot(path=str(tmp_path / "live-progress.png"), full_page=True)
                with session_scope() as session:
                    run = session.get(ScanRun, run_id)
                    run.status = "completed_with_warnings"
                    run.progress = 100
                expect(page.locator("#final-scan-link")).to_be_visible(timeout=7000)
                expect(page.locator("[data-progress=progress_text]")).to_contain_text(
                    "不代表覆盖完整"
                )
                if fallback:
                    assert any(
                        "/api/scans/" in url and not url.endswith("/events") for url in requests
                    )
            finally:
                browser.close()
