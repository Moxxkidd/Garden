"""Export lifecycle at the public submit/status/cancel/download boundary."""

from fastapi.testclient import TestClient

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.projects import ProjectQuery
from app.services.asset_delivery import AssetDeliveryService
from app.services.project_curation import ProjectCurationService
from tests.helpers.project_assets import project_fixture, source_run
from tests.test_project_comparison import ingest


class HeldExecutor:
    def __init__(self):
        self.pending = []

    def submit(self, job_id, callback):
        self.pending.append((job_id, callback))

    def run(self):
        job_id, callback = self.pending.pop(0)
        callback(job_id)

    def shutdown(self):
        pass


def test_export_lifecycle_never_downloads_partial_or_silently_restarts(
    app, seeded_records, tmp_path, monkeypatch
):
    from app.services.delivery_jobs import ExportJobService

    executor = HeldExecutor()
    service = ExportJobService(base_directory=tmp_path / "exports", dispatcher=executor)
    app.state.export_jobs = service
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        ingest(session, project, entry, source_run(session, target))
        first = AssetDeliveryService().page(session, project.id, ProjectQuery())
        pid, aid, revision = project.id, first["items"][0]["id"], first["revision"]
    client = TestClient(app)

    def submit(format="json"):
        response = client.post(f"/api/projects/{pid}/exports", json={"format": format})
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["status"] == "generating" and "artifact_path" not in job
        assert client.get(f"/api/exports/{job['id']}/download").status_code == 409
        return job["id"]

    changed = submit()
    with session_scope() as session:
        ProjectCurationService().annotate(session, pid, aid, [], "new revision", revision)
    executor.run()
    failure = client.get(f"/api/exports/{changed}").json()
    assert failure["status"] == "failed" and failure["error_code"] == "delivery_revision_changed"
    assert client.get(f"/api/exports/{changed}/download").status_code == 409
    cancelled = submit()
    assert client.post(f"/api/exports/{cancelled}/cancel").json()["status"] == "cancelled"
    executor.run()
    assert client.get(f"/api/exports/{cancelled}/download").status_code == 409
    interrupted = submit()
    restarted = ExportJobService(base_directory=tmp_path / "exports", dispatcher=HeldExecutor())
    assert restarted.interrupt_active() == 1
    state = client.get(f"/api/exports/{interrupted}").json()
    assert state["status"] == "failed" and state["error_code"] == "export_interrupted"
    executor.run()
    ready = submit()
    executor.run()
    state = client.get(f"/api/exports/{ready}").json()
    assert state["status"] == "ready" and state["download_url"].endswith("/download")
    download = client.get(state["download_url"])
    assert download.status_code == 200
    assert download.json()["exported_count"] == 1
    assert download.json()["items"][0]["note"] == "new revision"
    page = client.get(f"/exports/{ready}")
    assert "已生成" in page.text and "已保存到" not in page.text
    assert not list((tmp_path / "exports").glob("**/*.partial"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "missing-browser"))
    unavailable = submit("pdf")
    executor.run()
    assert client.get(f"/api/exports/{unavailable}").json()["error_code"] == "pdf_unavailable"
    assert client.get(f"/api/exports/{unavailable}/download").status_code == 409
    still_available = submit()
    executor.run()
    assert client.get(f"/api/exports/{still_available}/download").status_code == 200


def test_bundle_keeps_one_revision_and_offline_browser_download_feedback(
    app, seeded_records, tmp_path
):
    import csv
    import io
    import json
    import time
    import zipfile
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from playwright.sync_api import expect, sync_playwright
    from sqlalchemy import event

    from app.db.bootstrap import get_engine
    from app.schemas.projects import ProjectReviewRequest
    from app.services.delivery_jobs import ExportJobService
    from tests.test_scan_preview_e2e import _serve

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        for day, profile in ((1, None), (2, seeded_records["credential"].id)):
            run = source_run(
                session, target, day=day, profile_id=profile, path="/asset?token=synthetic-secret"
            )
            if profile:
                run.contexts[0].identity_snapshot = {"name": "=SUM(1,1)", "role": "reader"}
            ingest(session, project, entry, run)
        selected = AssetDeliveryService().page(session, project.id, ProjectQuery())
        pid, aid = project.id, selected["items"][0]["id"]
        old_note = '<img src="https://outside.invalid/exfil"> retained by reviewer'
        ProjectCurationService().annotate(session, pid, aid, [], old_note, selected["revision"])
        revision = selected["revision"] + 1
        ProjectCurationService().review(
            session,
            pid,
            ProjectReviewRequest(
                asset_ids=[aid], state="keep", reason="留存证据", expected_revision=revision
            ),
        )
        revision += 1
    engine = get_engine()
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    executor = HeldExecutor()
    app.state.export_jobs = ExportJobService(
        base_directory=tmp_path / "exports", dispatcher=executor
    )
    client = TestClient(app)
    response = client.post(
        f"/api/projects/{pid}/exports",
        json={"format": "zip", "expected_revision": revision, "query": {"page": 2, "page_size": 1}},
    )
    assert response.status_code == 202
    job = response.json()
    changed = []
    with ThreadPoolExecutor(max_workers=1) as pool:

        def annotate():
            with session_scope() as session:
                ProjectCurationService().annotate(session, pid, aid, [], "later revision", revision)

        def after_read(connection, cursor, statement, parameters, context, many):
            if "FROM projects" in statement and not changed:
                changed.append(True)
                pool.submit(annotate).result(timeout=10)

        event.listen(engine, "after_cursor_execute", after_read)
        try:
            executor.run()
        finally:
            event.remove(engine, "after_cursor_execute", after_read)
    assert changed
    state = client.get(f"/api/exports/{job['id']}").json()
    assert state["status"] == "ready"
    bundle = zipfile.ZipFile(io.BytesIO(client.get(state["download_url"]).content))
    metadata = json.loads(bundle.read("metadata.json"))
    assets = json.loads(bundle.read("assets.json"))
    readable = list(csv.DictReader(io.StringIO(bundle.read("readable.csv").decode("utf-8-sig"))))
    assert metadata["revision"] == revision == assets["revision"]
    assert assets["matched"] == assets["exported_count"] == 1
    assert assets["items"][0]["note"] == old_note
    assert len(readable) == 2 and len({r["身份"] for r in readable}) == 2
    assert any(r["身份"].startswith("'=SUM") for r in readable)
    assert {r["复核状态"] for r in readable} == {"人工保留"}
    html = bundle.read("report.html").decode()
    assert "&lt;img" in html and '<img src="https://outside.invalid' not in html
    assert "synthetic-secret" not in "".join(bundle.read(n).decode() for n in bundle.namelist())
    index = json.loads(bundle.read("sources.json"))
    assert index["revision"] == revision and len(index["items"]) == 2
    path = tmp_path / "report.html"
    path.write_text(html)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True)
        try:
            context = browser.new_context()
            outside = []
            context.route(
                "http**/*", lambda route: (outside.append(route.request.url), route.abort())
            )
            page = context.new_page()
            page.goto(path.as_uri())
            expect(page.get_by_role("heading", name="二 · 资产清单")).to_be_visible()
            assert page.locator("img,script,link").count() == 0 and not outside
        finally:
            browser.close()
    # Use the actual application lifecycle and executor for one native download journey.
    with _serve(app) as garden, sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(8000)
            page.goto(garden + f"/projects/{pid}/delivery")
            page.get_by_label("格式", exact=True).select_option("json")
            page.get_by_role("button", name="按当前修订生成文件").click()
            page.wait_for_url("**/exports/*")
            job_id = page.url.rsplit("/", 1)[1]
            deadline = time.monotonic() + 15
            while (
                page.request.get(garden + f"/api/exports/{job_id}").json()["status"] == "generating"
            ):
                assert time.monotonic() < deadline
                time.sleep(0.05)
            assert page.request.get(garden + f"/api/exports/{job_id}").json()["status"] == "ready"
            page.reload()
            expect(page.get_by_role("heading", name="已生成", exact=True)).to_be_visible()
            with page.expect_download() as download:
                page.get_by_role("link", name="下载已生成文件").click()
            download.value.save_as(tmp_path / "download.json")
            expect(page.get_by_role("status").filter(has_text="已发起下载")).to_be_visible()
            assert json.loads((tmp_path / "download.json").read_text())["revision"] == revision + 1
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            artifacts = Path("output/playwright/v150")
            artifacts.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(artifacts / "download-mobile.png"), full_page=True)
        finally:
            browser.close()
