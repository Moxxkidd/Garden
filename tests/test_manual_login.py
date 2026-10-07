import time

import pytest

from app.core.errors import InputValidationError
from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.identity import ManualLoginRequest
from app.services.identity_sessions import IdentitySessionService
from app.services.manual_login import ManualBrowser, ManualLoginService
from app.services.session_storage import SessionStorageService
from tests.fixtures.identity_site import identity_site
from tests.test_scan_preview_e2e import _serve


def finished(service, attempt_id, states):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with session_scope() as session:
            view = service.get(session, attempt_id)
        if view.state in states:
            return view
        time.sleep(0.05)
    pytest.fail("manual worker did not reach expected state")


def test_manual_challenge_saves_only_after_fresh_restore(seeded_records, tmp_path):
    fixture, state = identity_site()

    class HumanSimulation(ManualBrowser):
        def open(self):
            super().open()
            self.page.locator('[name="username"]').fill("reader")
            self.page.locator('[name="code"]').fill("123456")
            self.page.get_by_role("button", name="Login").click()
            self.page.wait_for_selector("#identity")

    with _serve(fixture) as base:
        sessions = IdentitySessionService(storage=SessionStorageService(tmp_path / "private"))
        service = ManualLoginService(
            sessions=sessions, driver_factory=lambda *args: HumanSimulation(*args, headless=True)
        )
        with session_scope() as session:
            session.get(Target, seeded_records["target"].id).base_url = base
            request = ManualLoginRequest(
                profile_id=seeded_records["credential"].id,
                login_url=base + "/login",
                validate_url=base + "/me",
                success_selector="#identity",
            )
            view = service.start(session, request)
        try:
            waiting = finished(service, view.id, {"waiting_for_user", "failed"})
            assert waiting.state == "waiting_for_user"
            assert waiting.session_id is None
            with session_scope() as session:
                service.confirm(session, view.id)
            ready = finished(service, view.id, {"ready", "failed"})
            assert ready.state == "ready" and ready.session_id
            assert "private-reader" not in ready.model_dump_json()
            with session_scope() as session:
                assert sessions.validate(session, ready.session_id).status == "ready"
        finally:
            service.shutdown()


class IdleBrowser:
    def __init__(self, *args):
        self.closed = False

    def open(self):
        pass

    def poll(self):
        time.sleep(0.01)

    def close(self):
        self.closed = True


def test_capacity_cancel_and_interrupted_cleanup(seeded_records):
    service = ManualLoginService(driver_factory=IdleBrowser)
    request = ManualLoginRequest(
        profile_id=seeded_records["credential"].id,
        login_url="http://localhost:8080/login",
        validate_url="http://localhost:8080/me",
        success_text="signed in",
    )
    try:
        with session_scope() as session:
            views = [service.start(session, request) for _ in range(3)]
            with pytest.raises(InputValidationError):
                service.start(session, request)
        with session_scope() as session:
            service.cancel(session, views[0].id)
        assert finished(service, views[0].id, {"cancelled"}).state == "cancelled"
        assert service.shutdown() is None
        with session_scope() as session:
            assert service.get(session, views[1].id).state == "cancelled"
    finally:
        service.shutdown()


def test_expiry_and_worker_start_failure_never_become_ready(seeded_records):
    now = [0.0]
    service = ManualLoginService(driver_factory=IdleBrowser, clock=lambda: now[0])
    request = ManualLoginRequest(
        profile_id=seeded_records["credential"].id,
        login_url="http://localhost:8080/login",
        validate_url="http://localhost:8080/me",
        success_text="signed in",
    )
    try:
        with session_scope() as session:
            attempt = service.start(session, request)
        finished(service, attempt.id, {"waiting_for_user"})
        now[0] = 901
        assert finished(service, attempt.id, {"expired"}).session_id is None
    finally:
        service.shutdown()

    class NoDisplay(IdleBrowser):
        def open(self):
            raise RuntimeError("PRIVATE failure detail")

    service = ManualLoginService(driver_factory=NoDisplay)
    with session_scope() as session:
        attempt = service.start(session, request)
    failed = finished(service, attempt.id, {"failed"})
    assert "PRIVATE" not in failed.model_dump_json()
    service.shutdown()


def test_restart_marks_stale_attempt_interrupted(seeded_records):
    from datetime import datetime, timedelta, timezone

    from app.models.login_attempt import LoginAttempt

    service = ManualLoginService(driver_factory=IdleBrowser)
    with session_scope() as session:
        row = LoginAttempt(
            profile_id=seeded_records["credential"].id,
            state="waiting_for_user",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            owner_token="old-process",
            configuration={},
        )
        session.add(row)
        session.flush()
        assert service.cleanup_interrupted(session) == 1
        assert row.state == "interrupted"
