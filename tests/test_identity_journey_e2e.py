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
                assert status == "completed", app.state.scan_service.get_scan(run_id).model_dump()
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


def test_multi_entry_manual_post_budget_and_expiry_journey(app, seeded_records, tmp_path):
    """One synthetic journey, including SSO-domain separation and safe GET-only recovery."""
    import threading

    from playwright.sync_api import expect
    from sqlalchemy import select

    from app.models.collection_queue import CollectionWork
    from app.models.credential_profile import CredentialProfile
    from app.models.scan_request import ScanRequest
    from app.schemas.identity import IdentityCollectionRequest, ManualLoginRequest, RecoveryRequest
    from app.schemas.projects import ProjectEntryCreate, ProjectQuery
    from app.schemas.scan import ScanOptions
    from app.services.identity_recovery import IdentityRecoveryService
    from app.services.manual_collection import ManualCollectionControl
    from app.services.project_catalog import ProjectCatalogService
    from app.services.projects import ProjectService
    from app.services.scan_reporting import ScanReportService

    first, one = identity_site()
    second, two = identity_site()
    idp, sso = identity_site()
    one["manual"] = True
    controls = []
    behavior = ["stop"]
    opened = threading.Event()

    class Human(ManualBrowser):
        def open(self):
            super().open()
            self.page.locator('[name="username"]').fill(
                "reader" if self.config.profile_id == seeded_records["credential"].id else "other"
            )
            self.page.locator('[name="code"]').fill("123456")
            self.page.get_by_role("button", name="Login", exact=True).click()
            self.page.wait_for_selector("#identity")

    def control_factory():
        c = ManualCollectionControl(headless=True)

        def user_actions(page):
            opened.set()
            page.get_by_role(
                "button",
                name={"stop": "User POST", "budget": "Use budget", "expire": "Expire identity"}[
                    behavior[0]
                ],
                exact=True,
            ).click()

        c.on_open = user_actions
        controls.append(c)
        return c

    def wait_run(run_id):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            with session_scope() as session:
                row = session.get(ScanRun, run_id)
                if row.status not in {"queued", "running"}:
                    from app.services.project_ingestion import ProjectIngestionService

                    indexed = [
                        r
                        for r in ProjectIngestionService().runs(session, pid)
                        if r.source == "scan" and r.run_id == run_id
                    ]
                    assert not any(r.status == "failed" for r in indexed), [
                        (r.status, r.error_code) for r in indexed
                    ]
                    if not indexed or all(r.status == "complete" for r in indexed):
                        return row.status
            time.sleep(0.05)
        pytest.fail("child did not finish")

    with _serve(first) as base1, _serve(second) as base2, _serve(idp) as raw_idp:
        auth = raw_idp.replace("127.0.0.1", "localhost")
        sso["return_to"] = base1 + "/sso-return"
        one["cross_origin"] = base2
        with session_scope() as session:
            target = session.get(Target, seeded_records["target"].id)
            target.base_url = base1
            other = Target(
                name="second-origin",
                base_url=base2,
                type="web",
                owner="fixture",
                tags=[],
                status="active",
            )
            session.add(other)
            session.flush()
            profile = CredentialProfile(
                target_id=other.id,
                name="Other identity",
                role="reader",
                auth_type="cookie",
                username="other",
                secret_ref="unused",
                login_config_path="unused",
            )
            session.add(profile)
            session.flush()
            projects = ProjectService()
            project = projects.create(session, "Multi entry", "")
            entries = [
                projects.add_entry(
                    session,
                    project.id,
                    ProjectEntryCreate(target_id=t.id, name=name, entry_url=t.base_url + "/me"),
                )
                for t, name in [(target, "First"), (other, "Second")]
            ]
            pid, p1, p2, e1, e2 = (
                project.id,
                seeded_records["credential"].id,
                profile.id,
                entries[0].id,
                entries[1].id,
            )
        with _serve(app) as garden:
            collection = app.state.identity_collection
            collection.pipeline.report_service = ScanReportService(tmp_path / "reports")
            collection.manual_control_factory = control_factory
            collection.manual_driver_factory = lambda *a, **kw: Human(*a, headless=True, **kw)
            app.state.manual_login.driver_factory = lambda *a: Human(*a, headless=True)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="chromium")
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                try:
                    # Continuous native login keeps the entry binding through proof.
                    for profile_id, eid, base, login in [
                        (p1, e1, base1, auth + "/login"),
                        (p2, e2, base2, base2 + "/login"),
                    ]:
                        page.goto(garden + f"/identities?project_entry_id={eid}")
                        form = page.locator('form[action="/identities/auth/login"]')
                        form.locator('[name="profile_id"]').select_option(str(profile_id))
                        form.locator('[name="login_url"]').fill(login)
                        form.locator('[name="validate_url"]').fill(base + "/me")
                        form.locator('[name="success_selector"]').fill("#identity")
                        if profile_id == p1:
                            form.locator('[name="allowed_auth_origins"]').fill(auth)
                        form.get_by_role("button", name="打开人工登录窗口", exact=True).click()
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
                        page.get_by_role("link", name="返回身份与采集", exact=True).click()
                        expect(page).to_have_url(
                            garden + f"/identities?project_entry_id={eid}#collection-start"
                        )
                    page.goto(garden + f"/projects/{pid}/identity-plan")
                    page.locator(f'[name="entry_ids"][value="{e1}"]').check()
                    page.locator(f'[name="entry_ids"][value="{e2}"]').check()
                    page.locator(f'[name="profile_{e1}"]').select_option(str(p1))
                    page.locator(f'[name="profile_{e2}"]').select_option(str(p2))
                    page.locator(f'[name="mode_{e1}"]').select_option("manual")
                    page.locator('[name="total_requests"]').fill("40")
                    page.get_by_role("button", name="预览全部入口与预算").click()
                    assert "固定分配 40" in page.inner_text("body"), page.inner_text("body")
                    page.get_by_role("button", name="确认提交整个计划").click()
                    plan_id = int(page.url.rsplit("/", 1)[1])
                    plan = page.request.get(garden + f"/api/identity-plans/{plan_id}").json()
                    assert [i["max_requests"] for i in plan["items"]] == [20, 20]
                    assert opened.wait(15)
                    assert one["post_started"].wait(5)
                    first_run, second_run = [i["run_id"] for i in plan["items"]]
                    page.goto(garden + f"/identities/runs/{first_run}/manual")
                    expect(page.get_by_role("status")).to_contain_text("人工浏览采集已开启")
                    page.get_by_role("button", name="停止人工浏览采集").click()
                    assert wait_run(first_run) == "completed_with_warnings"
                    assert wait_run(second_run) == "completed", app.state.scan_service.get_scan(
                        second_run
                    ).model_dump()
                    assert one["posts"] == ["reader"]
                    assert two["posts"] == []
                    with session_scope() as session:
                        captured = list(
                            session.scalars(
                                select(ScanRequest).where(ScanRequest.scan_run_id == first_run)
                            )
                        )
                        assert any(
                            r.method == "POST"
                            and r.capture_source == "manual"
                            and not r.replay_allowed
                            and r.replay_status == "not_allowed"
                            for r in captured
                        )
                        assert all(not r.normalized_redacted_url.startswith(auth) for r in captured)
                        assert (
                            session.scalar(
                                select(CollectionWork).where(CollectionWork.method == "POST")
                            )
                            is None
                        )
                        catalog = ProjectCatalogService().list(
                            session, pid, ProjectQuery(view="observations")
                        )
                        manual_posts = [o for o in catalog.items if o["record"]["method"] == "POST"]
                        assert len(manual_posts) == 1
                        assert manual_posts[0]["record"]["identity_assessment"] == "confirmed"
                        assert "人工浏览" in manual_posts[0]["record"]["provenance_note"]
                        assert (
                            sum(
                                session.get(ScanRun, rid)
                                .contexts[0]
                                .identity_snapshot["request_attempts"]
                                for rid in [first_run, second_run]
                            )
                            <= 40
                        )
                        assert {o["identity_key"] for o in catalog.items} == {
                            f"target:{target.id}:profile:{p1}",
                            f"target:{other.id}:profile:{p2}",
                        }
                    assert not any(path == "foreign-only" for path, _ in two["requests"])
                    # The combined manual entry also closes login before a fresh proof.
                    behavior[0] = "stop"
                    opened.clear()
                    one["post_started"].clear()
                    with session_scope() as session:
                        inline_run = collection.start(
                            session,
                            IdentityCollectionRequest(
                                project_entry_id=e1,
                                target_id=target.id,
                                url=base1 + "/me",
                                profile_ids=[p1],
                                interaction_mode="manual",
                                manual_login=ManualLoginRequest(
                                    profile_id=p1,
                                    project_entry_id=e1,
                                    login_url=auth + "/login",
                                    validate_url=base1 + "/me",
                                    success_selector="#identity",
                                    allowed_auth_origins=[auth],
                                ),
                                options=ScanOptions(max_browser_requests=20),
                            ),
                        )
                    page.goto(garden + f"/identities/runs/{inline_run}/manual")
                    deadline = time.monotonic() + 20
                    while not page.get_by_role(
                        "button", name="已完成登录，恢复验证并开始记录"
                    ).count():
                        assert time.monotonic() < deadline
                        page.wait_for_timeout(100)
                        page.reload()
                    page.get_by_role("button", name="已完成登录，恢复验证并开始记录").click()
                    assert opened.wait(15) and one["post_started"].wait(5)
                    page.reload()
                    page.get_by_role("button", name="停止人工浏览采集").click()
                    assert wait_run(inline_run) == "completed_with_warnings"
                    for mode, quota in [("budget", 6), ("expire", 20)]:
                        behavior[0] = mode
                        opened.clear()
                        with session_scope() as session:
                            run_id = collection.start(
                                session,
                                IdentityCollectionRequest(
                                    project_entry_id=e1,
                                    target_id=target.id,
                                    url=base1 + "/me",
                                    profile_ids=[p1],
                                    interaction_mode="manual",
                                    options=ScanOptions(max_browser_requests=quota),
                                ),
                            )
                        assert opened.wait(15)
                        assert wait_run(run_id) == "completed_with_warnings"
                        with session_scope() as session:
                            run = session.get(ScanRun, run_id)
                            assert run.contexts[0].identity_snapshot["request_attempts"] <= quota
                            assert run.completeness == "incomplete"
                    # Re-login then explicitly preview only the known GET/HEAD uncertainty.
                    with session_scope() as session:
                        original = session.get(ScanRun, run_id)
                        context = original.contexts[0]
                        recovery = IdentityRecoveryService(
                            storage=collection.storage, collection=collection
                        )
                        request = RecoveryRequest(
                            source_run_id=run_id,
                            source_context_id=context.id,
                            checkpoint_version=context.identity_snapshot["checkpoint_version"],
                        )
                        preview = recovery.preview(session, request)
                        assert preview.uncertain_count + preview.pending_count > 0
                        assert "POST" not in preview.model_dump_json()
                    from tests.test_manual_login import finished

                    with session_scope() as session:
                        attempt = app.state.manual_login.start(
                            session,
                            ManualLoginRequest(
                                profile_id=p1,
                                project_entry_id=e1,
                                login_url=auth + "/login",
                                validate_url=base1 + "/me",
                                success_selector="#identity",
                                allowed_auth_origins=[auth],
                            ),
                        )
                    assert (
                        finished(
                            app.state.manual_login, attempt.id, {"waiting_for_user", "failed"}
                        ).state
                        == "waiting_for_user"
                    )
                    with session_scope() as session:
                        app.state.manual_login.confirm(session, attempt.id)
                    assert (
                        finished(app.state.manual_login, attempt.id, {"ready", "failed"}).state
                        == "ready"
                    )
                    posts_before = len(one["posts"])
                    with session_scope() as session:
                        preview = recovery.preview(session, request)
                        recovered = recovery.start(session, request, preview.preview_token)
                    assert wait_run(recovered) == "completed"
                    with session_scope() as session:
                        assert session.get(ScanRun, run_id).completeness == "incomplete"
                        assert session.get(ScanRun, recovered).parent_run_id == run_id
                        assert all(
                            r.method in {"GET", "HEAD"}
                            for r in session.scalars(
                                select(ScanRequest).where(ScanRequest.scan_run_id == recovered)
                            )
                        )
                    assert len(one["posts"]) == posts_before
                    assert all(
                        who == "other" for path, who in two["requests"] if path in {"me", "asset"}
                    )
                    from pathlib import Path

                    screenshots = Path("output/playwright/v140")
                    screenshots.mkdir(parents=True, exist_ok=True)
                    page.goto(garden + f"/identities/plans/{plan_id}")
                    page.screenshot(path=str(screenshots / "plan-desktop.png"), full_page=True)

                    page.goto(garden + f"/projects/{pid}/assets")
                    assert "SYNTHETIC_PRIVATE" not in page.content()
                    assert "private-reader" not in page.content()
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.goto(garden + f"/identities/runs/{first_run}/manual")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.screenshot(path=str(screenshots / "manual-mobile.png"), full_page=True)
                finally:
                    browser.close()
