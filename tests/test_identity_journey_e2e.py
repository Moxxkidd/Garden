"""Real browser native forms: manual challenge -> proof -> preview -> catalog."""

import time

import pytest
from playwright.sync_api import sync_playwright

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanRun
from app.models.target import Target
from app.services.manual_login import ManualBrowser
from tests.fixtures.identity_site import identity_site
from tests.test_scan_preview_e2e import _serve


@pytest.mark.parametrize("project_linked,profile_count", [(False, 1), (True, 1), (True, 2)])
def test_desktop_mobile_identity_journey_without_javascript(
    app, seeded_records, tmp_path, project_linked, profile_count
):
    fixture, _ = identity_site()
    usernames = iter(["reader", "second-reader"])
    profile_ids = [seeded_records["credential"].id]

    class HumanSimulation(ManualBrowser):
        def open(self):
            super().open()
            self.page.locator('[name="username"]').fill(next(usernames))
            self.page.locator('[name="code"]').fill("123456")
            self.page.get_by_role("button", name="Login").click()
            self.page.wait_for_selector("#identity")

    with _serve(fixture) as base:
        with session_scope() as session:
            target = session.get(Target, seeded_records["target"].id)
            target.base_url = base
            if profile_count == 2:
                from app.models.credential_profile import CredentialProfile

                original = session.get(CredentialProfile, profile_ids[0])
                second = CredentialProfile(
                    target_id=target.id,
                    name=original.name + "-second",
                    role=original.role,
                    auth_type="cookie",
                    username="second-reader",
                    secret_ref="unused",
                    login_config_path="unused",
                )
                session.add(second)
                session.flush()
                profile_ids.append(second.id)
            if project_linked:
                from app.schemas.projects import ProjectEntryCreate
                from app.services.projects import ProjectService

                projects = ProjectService()
                project = projects.create(session, "Authenticated journey", "")
                entry = projects.add_entry(
                    session,
                    project.id,
                    ProjectEntryCreate(
                        target_id=target.id,
                        entry_url=base + "/me?tenant=fixture",
                        name="Signed-in entry",
                    ),
                )
                project_id, entry_id = project.id, entry.id
        with _serve(app) as garden:
            app.state.manual_login.driver_factory = lambda *args: HumanSimulation(
                *args, headless=True
            )
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, channel="chromium")
                context = browser.new_context(
                    java_script_enabled=False, viewport={"width": 1280, "height": 900}
                )
                page = context.new_page()
                for profile_id in profile_ids:
                    page.goto(garden + "/identities")
                    page.locator('[name="profile_id"]').select_option(str(profile_id))
                    page.locator('[name="login_url"]').fill(base + "/login")
                    page.locator('[name="validate_url"]').fill(base + "/me")
                    page.locator('[name="success_selector"]').fill("#identity")
                    page.get_by_role("button", name="打开人工登录窗口").click()
                    assert "/identities/login/" in page.url, page.inner_text("body")
                    deadline = time.monotonic() + 20
                    while not page.get_by_role("button", name="已完成登录，验证并保存").count():
                        assert time.monotonic() < deadline
                        page.wait_for_timeout(100)
                        page.reload()
                    page.get_by_role("button", name="已完成登录，验证并保存").click()
                    while "恢复验证已通过" not in page.inner_text("body"):
                        assert time.monotonic() < deadline
                        page.wait_for_timeout(100)
                        page.reload()
                    assert "尚未开始扫描" in page.inner_text("body")
                page.goto(
                    garden
                    + (
                        f"/identities?project_entry_id={entry_id}"
                        if project_linked
                        else "/identities"
                    )
                )
                page.locator('[name="url"]').fill(
                    base + ("/me?tenant=fixture" if project_linked else "/me")
                )
                for profile_id in profile_ids:
                    page.locator(f'[name="profile_ids"][value="{profile_id}"]').check()
                page.get_by_role("button", name="预览采集范围").click()
                assert "确认身份采集范围" in page.inner_text("body")
                page.get_by_role("button", name="确认开始采集").click()
                run_id = int(page.url.rstrip("/").split("/")[-1])
                deadline = time.monotonic() + 30
                while True:
                    with session_scope() as session:
                        status = session.get(ScanRun, run_id).status
                    if status not in {"queued", "running"}:
                        break
                    assert time.monotonic() < deadline
                    page.wait_for_timeout(100)
                assert status == "completed", app.state.scan_service.get_assessment(
                    run_id
                ).model_dump()
                page.goto(garden + f"/assets?source=scan&run_id={run_id}&view=grouped")
                assert "身份可见性矩阵" in page.inner_text("body")
                assert "已观察" in page.inner_text("body")
                assert "private-reader" not in page.content()
                import json

                catalog = page.request.get(
                    garden + f"/api/assets?source=scan&run_id={run_id}&view=grouped"
                ).json()
                (tmp_path / "identity-demo.json").write_text(
                    json.dumps(
                        {
                            "status": status,
                            "subject_count": catalog["total"],
                            "identity_matrix": catalog["identity_matrix"],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                page.screenshot(path=str(tmp_path / "identity-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(garden + "/identities")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(tmp_path / "identity-mobile.png"), full_page=True)
                if project_linked:
                    from pathlib import Path

                    from app.schemas.projects import ProjectQuery
                    from app.services.project_catalog import ProjectCatalogService

                    with session_scope() as session:
                        result = ProjectCatalogService().list(
                            session, project_id, ProjectQuery(view="observations")
                        )
                        assert result.matched > 0
                        assert {o["identity_key"] for o in result.items} == {
                            f"target:{target.id}:profile:{profile_id}" for profile_id in profile_ids
                        }
                        assert result.matched == 2 * profile_count
                        assert all(
                            o["record"]["identity_assessment"] == "confirmed" for o in result.items
                        )
                    page.goto(garden + f"/projects/{project_id}/assets")
                    assert "项目资产清单" in page.inner_text("body")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    artifacts = Path("output/playwright/v07")
                    artifacts.mkdir(parents=True, exist_ok=True)
                    page.screenshot(
                        path=str(artifacts / "project-authenticated-mobile.png"), full_page=True
                    )
                browser.close()
