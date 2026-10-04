"""Worker-owned manual browser login; no automatic challenge handling."""

from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import select

from app.core.errors import InputValidationError
from app.db.bootstrap import session_scope
from app.models.login_attempt import LoginAttempt
from app.schemas.identity import LoginAttemptView, ManualLoginRequest, StoredIdentityState
from app.services.discovery import origin
from app.services.identity_sessions import IdentitySessionService

TERMINAL = {"ready", "failed", "cancelled", "expired", "interrupted"}


class ManualBrowser:
    def __init__(self, config, target_origin, policy, *, headless=False):
        self.config, self.target_origin, self.policy = config, target_origin, policy
        self.headless = headless
        self.pw = self.browser = self.context = self.page = None

    def open(self):
        from playwright.sync_api import sync_playwright

        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(channel="chromium", headless=self.headless)
        self.context = self.browser.new_context(service_workers="block")
        allowed = {self.target_origin, *self.config.allowed_auth_origins}

        def admit(route):
            try:
                value = self.policy.normalize_url(route.request.url)
                if origin(value) not in allowed:
                    route.abort()
                    return
                self.policy.ensure_destination_allowed(value)
                route.continue_()
            except Exception:
                route.abort()

        self.context.route("**/*", admit)
        self.context.route_web_socket("**/*", lambda socket: socket.close())
        self.page = self.context.new_page()
        self.page.goto(self.config.login_url, wait_until="domcontentloaded", timeout=15000)

    def poll(self):
        if not self.context.pages:
            raise InputValidationError("登录窗口已关闭。")
        self.context.pages[-1].wait_for_timeout(100)

    def snapshot(self):
        stored = self.context.storage_state()
        host = urlsplit(self.target_origin).hostname
        stored["cookies"] = [
            c for c in stored["cookies"] if c["domain"].lstrip(".").lower() == host
        ]
        stored["origins"] = [o for o in stored["origins"] if o["origin"] == self.target_origin]
        values = {}
        for page in self.context.pages:
            if origin(page.url) == self.target_origin:
                values[self.target_origin] = page.evaluate(
                    "Object.fromEntries(Object.entries(sessionStorage))"
                )
        return StoredIdentityState(
            target_origin=self.target_origin, storage_state=stored, session_storage=values
        )

    def close(self):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self.pw:
                self.pw.stop()


class _Worker:
    def __init__(self, attempt_id, config, base, service):
        self.id, self.config, self.base, self.service = attempt_id, config, base, service
        self.commands = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=self.run, name=f"garden-login-{attempt_id}", daemon=True
        )
        self.deadline = service.clock() + 900

    def check(self):
        if self.stop.is_set() or self.service.clock() >= self.deadline:
            raise InputValidationError("人工登录已取消或过期。")

    def run(self):
        driver = None
        result = None
        try:
            driver = self.service.driver_factory(
                self.config, self.base, self.service.sessions.policy
            )
            self.check()
            driver.open()
            self.service._state(self.id, "waiting_for_user")
            while not self.stop.is_set():
                if self.service.clock() >= self.deadline:
                    self.service._state(self.id, "expired", "login_expired")
                    break
                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    driver.poll()
                    continue
                if command == "confirm":
                    self.service._state(self.id, "validating_restore")
                    state = driver.snapshot()
                    self.check()
                    # Close the original browser before restoring; all proof comes from a fresh one.
                    driver.close()
                    driver = None
                    with session_scope() as session:
                        result = self.service.sessions.import_state(
                            session,
                            self.config.profile_id,
                            state.model_dump_json(),
                            self.config,
                            before_request=self.check,
                        )
                    self.check()
                    self.service._state(
                        self.id,
                        "ready" if result.status == "ready" else "failed",
                        result.reason_code,
                        result.session_id,
                    )
                    break
        except Exception:
            self.service._state(
                self.id, "cancelled" if self.stop.is_set() else "failed", "manual_login_unavailable"
            )
        finally:
            if driver:
                try:
                    driver.close()
                except Exception:
                    pass
            if self.stop.is_set():
                self.service._state(self.id, "cancelled")
            if result and result.session_id:
                with session_scope() as session:
                    attempt = session.get(LoginAttempt, self.id)
                    if not attempt or attempt.state != "ready":
                        self.service.sessions.revoke(session, result.session_id)
            with self.service._lock:
                self.service._workers.pop(self.id, None)


class ManualLoginService:
    _lock = threading.RLock()
    _workers = {}
    _owner = uuid4().hex

    def __init__(self, *, sessions=None, driver_factory=ManualBrowser, clock=time.monotonic):
        self.sessions = sessions or IdentitySessionService()
        self.driver_factory = driver_factory
        self.clock = clock
        self._owned = set()

    def cleanup_interrupted(self, session):
        count = 0
        for record in session.scalars(
            select(LoginAttempt).where(LoginAttempt.state.not_in(TERMINAL))
        ):
            if record.owner_token != self._owner or record.id not in self._workers:
                record.state, record.reason_code = "interrupted", "worker_unavailable"
                count += 1
        session.flush()
        return count

    def start(self, session, request: ManualLoginRequest):
        _, target, config = self.sessions.configuration(session, request.profile_id, request)
        with self._lock:
            self.cleanup_interrupted(session)
            if len(self._workers) >= 3:
                raise InputValidationError("最多同时打开 3 个人工登录窗口，请完成或取消已有登录。")
            record = LoginAttempt(
                profile_id=request.profile_id,
                state="created",
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=900),
                owner_token=self._owner,
                configuration={"target_id": target.id},
            )
            session.add(record)
            session.flush()
            worker = _Worker(record.id, config, origin(target.base_url), self)
            self._workers[record.id] = worker
            self._owned.add(record.id)
            session.commit()
            worker.thread.start()
            return self._view(record)

    def _view(self, record):
        return LoginAttemptView(
            id=record.id,
            state=record.state,
            expires_at=record.expires_at,
            session_id=record.session_id,
            reason_code=record.reason_code,
        )

    def get(self, session, attempt_id):
        self.cleanup_interrupted(session)
        record = session.get(LoginAttempt, attempt_id, populate_existing=True)
        if not record:
            raise InputValidationError("登录尝试不存在。")
        return self._view(record)

    def confirm(self, session, attempt_id):
        view = self.get(session, attempt_id)
        with self._lock:
            worker = self._workers.get(attempt_id)
            if view.state != "waiting_for_user" or not worker:
                raise InputValidationError("登录尚未就绪或已经结束。")
            try:
                worker.commands.put_nowait("confirm")
            except queue.Full:
                raise InputValidationError("正在验证恢复，请勿重复确认。") from None
        return view

    def cancel(self, session, attempt_id):
        view = self.get(session, attempt_id)
        if view.state in TERMINAL:
            return view
        with self._lock:
            worker = self._workers.get(attempt_id)
            if worker:
                worker.stop.set()
            record = session.get(LoginAttempt, attempt_id)
            record.state = "cancelled"
            session.commit()
        return self._view(record)

    def _state(self, attempt_id, state, reason=None, session_id=None):
        with self._lock, session_scope() as session:
            record = session.get(LoginAttempt, attempt_id)
            if record and record.state not in TERMINAL:
                record.state, record.reason_code, record.session_id = state, reason, session_id

    def shutdown(self):
        workers = [self._workers[i] for i in list(self._owned) if i in self._workers]
        for worker in workers:
            worker.stop.set()
        for worker in workers:
            worker.thread.join(timeout=20)
