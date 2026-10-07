"""Identity state admission and fresh-context proof; never exposes private payloads."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.core.errors import InputValidationError
from app.core.settings import get_settings
from app.models.auth_session import AuthSession
from app.models.credential_profile import CredentialProfile
from app.models.target import Target
from app.schemas.identity import ManualLoginRequest, SessionHealthView, StoredIdentityState
from app.services.authenticated_network import AuthenticatedNetworkGuard
from app.services.discovery import origin
from app.services.scan_network import TargetNetworkPolicy
from app.services.session_storage import SessionStorageService


def parse_identity_state(raw_json: str, target_origin: str) -> StoredIdentityState:
    """Strict target-bound JSON; ordinary Playwright and Garden state formats only."""
    try:
        if len(raw_json.encode("utf-8")) > 1048576:
            raise ValueError
        data = json.loads(raw_json)
        if not isinstance(data, dict):
            raise ValueError
        if "version" in data:
            state = StoredIdentityState.model_validate(data)
            if state.target_origin != target_origin:
                raise ValueError
        else:
            state = StoredIdentityState(target_origin=target_origin, storage_state=data)
        stored = state.storage_state
        if set(stored) != {"cookies", "origins"}:
            raise ValueError
        if not isinstance(stored["cookies"], list) or not isinstance(stored["origins"], list):
            raise ValueError
        host = urlsplit(target_origin).hostname
        for cookie in stored["cookies"]:
            if not isinstance(cookie, dict) or set(cookie) - {
                "name",
                "value",
                "domain",
                "path",
                "expires",
                "httpOnly",
                "secure",
                "sameSite",
            }:
                raise ValueError
            if any(not isinstance(cookie.get(k), str) for k in ["name", "value", "domain", "path"]):
                raise ValueError
            if cookie["domain"].lstrip(".").lower() != host or not cookie["path"].startswith("/"):
                raise ValueError
            if not cookie["name"] or any(ord(c) < 32 for c in cookie["name"] + cookie["domain"]):
                raise ValueError
            if cookie.get("sameSite", "Lax") not in {"Strict", "Lax", "None"}:
                raise ValueError
            if any(k in cookie and type(cookie[k]) is not bool for k in ["httpOnly", "secure"]):
                raise ValueError
            if "expires" in cookie and type(cookie["expires"]) not in {int, float}:
                raise ValueError
        for item in stored["origins"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"origin", "localStorage"}
                or item["origin"] != target_origin
            ):
                raise ValueError
            if not isinstance(item["localStorage"], list):
                raise ValueError
            for pair in item["localStorage"]:
                if (
                    not isinstance(pair, dict)
                    or set(pair) != {"name", "value"}
                    or any(not isinstance(v, str) for v in pair.values())
                ):
                    raise ValueError
        if any(o != target_origin for o in state.session_storage):
            raise ValueError
        return state
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise InputValidationError(
            "登录状态格式、大小或目标范围无效；仅支持最多 1 MiB 的目标站点状态。"
        ) from None


def install_session_storage(context, state: StoredIdentityState, page):
    # Hydrate each tab only once. Replaying an init script on every document
    # would restore credentials that the application deliberately cleared.
    def attach(page):
        cdp = context.new_cdp_session(page)
        cdp.send("Page.enable")
        scripts = {}
        for site_origin, values in state.session_storage.items():
            source = (
                f"if (location.origin === {json.dumps(site_origin)}) {{"
                f"const values = {json.dumps(values, ensure_ascii=True)};"
                "for (const [k, v] of Object.entries(values)) sessionStorage.setItem(k, v); }"
            )
            result = cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": source})
            scripts[site_origin] = result["identifier"]

        def navigated():
            site_origin = origin(page.url)
            identifier = scripts.pop(site_origin, None)
            if identifier is not None:
                cdp.send("Page.removeScriptToEvaluateOnNewDocument", {"identifier": identifier})

        page.on("domcontentloaded", navigated)

    if state.session_storage:
        attach(page)


class IdentitySessionService:
    def __init__(self, *, storage=None, policy=None, verifier=None):
        self.storage = storage or SessionStorageService()
        self.policy = policy or TargetNetworkPolicy(get_settings())
        self.guard = AuthenticatedNetworkGuard(self.policy)
        self.verifier = verifier or self._verify

    def configuration(self, session: Session, profile_id: int, config: ManualLoginRequest):
        profile = session.get(CredentialProfile, profile_id)
        if not profile or config.profile_id != profile_id:
            raise InputValidationError("身份档案不存在或不匹配。")
        target = session.get(Target, profile.target_id)
        if not target or target.status != "active":
            raise InputValidationError("目标不可用于认证。")
        base = self.policy.normalize_url(target.base_url)
        validate_url = self.guard.ensure_allowed(base, config.validate_url)
        allowed = {origin(base)}
        for value in config.allowed_auth_origins:
            normalized = self.policy.normalize_url(value)
            if normalized.rstrip("/") != origin(normalized):
                raise InputValidationError("认证白名单必须是精确 origin，不含路径或查询。")
            self.policy.ensure_destination_allowed(normalized)
            allowed.add(origin(normalized))
        login_url = self.policy.normalize_url(config.login_url)
        self.policy.ensure_destination_allowed(login_url)
        if origin(login_url) not in allowed:
            raise InputValidationError("登录地址不在显式认证范围内。")
        return (
            profile,
            target,
            config.model_copy(
                update={
                    "login_url": login_url,
                    "validate_url": validate_url,
                    "allowed_auth_origins": sorted(allowed - {origin(base)}),
                }
            ),
        )

    def _verify(self, state: StoredIdentityState, config: ManualLoginRequest, before_request):
        from playwright.sync_api import Error, sync_playwright

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="chromium", headless=True)
                try:
                    context = browser.new_context(
                        storage_state=state.storage_state, service_workers="block"
                    )
                    context.route_web_socket("**/*", lambda socket: socket.close())
                    errors = []
                    attempts = 0

                    def admit(route):
                        nonlocal attempts
                        try:
                            if route.request.method not in {"GET", "HEAD"} or attempts >= 30:
                                route.abort()
                                return
                            self.guard.ensure_allowed(state.target_origin, route.request.url)
                            before_request()
                            attempts += 1
                            route.continue_()
                        except Exception as error:
                            errors.append(error)
                            route.abort()

                    context.route("**/*", admit)
                    page = context.new_page()
                    install_session_storage(context, state, page)
                    response = page.goto(
                        config.validate_url, wait_until="domcontentloaded", timeout=10000
                    )
                    if errors:
                        raise errors[0]
                    if response is None or not 200 <= response.status < 300:
                        return False
                    if config.success_selector:
                        page.locator(config.success_selector).first.wait_for(
                            state="visible", timeout=3000
                        )
                    if config.success_text and config.success_text not in page.locator(
                        "body"
                    ).inner_text(timeout=3000):
                        return False
                    return True
                finally:
                    browser.close()
        except Error:
            return False

    def import_state(
        self, session, profile_id, raw_json, verification, before_request=lambda: None
    ):
        profile, target, config = self.configuration(session, profile_id, verification)
        state = parse_identity_state(raw_json, origin(target.base_url))
        now = datetime.now(timezone.utc)
        if not self.verifier(state, config, before_request):
            return SessionHealthView(
                status="validation_failed", checked_at=now, reason_code="restore_not_verified"
            )
        before_request()
        record = AuthSession(
            target_id=target.id,
            credential_profile_id=profile.id,
            status="active",
            session_type="playwright_storage_state",
            refresh_supported=False,
            last_validated_at=now,
            storage_ref="",
            identity_metadata={"version": 1},
            session_metadata_redacted={"adapter": "manual", "restored": True},
        )
        path = None
        try:
            with session.begin_nested():
                session.add(record)
                session.flush()
                path = self.storage.write_payload(
                    record.id, {"identity": state.model_dump(), "verification": config.model_dump()}
                )
                record.storage_ref = path
                session.flush()
        except Exception:
            if path:
                Path(path).unlink(missing_ok=True)
            raise InputValidationError("保存登录状态失败，请重新认证。") from None
        self._audit(session, "identity_import", record)
        return SessionHealthView(session_id=record.id, status="ready", checked_at=now)

    def _record(self, session, session_id):
        record = session.get(AuthSession, session_id, populate_existing=True)
        if not record or record.revoked_at or not record.identity_metadata:
            raise InputValidationError("会话不可用、已撤销或不支持身份恢复。")
        if record.expires_at and record.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(
            timezone.utc
        ):
            raise InputValidationError("会话已过期，请重新认证。")
        return record

    def load_for_collection(self, session, session_id):
        record = self._record(session, session_id)
        payload = self.storage.read_identity_payload(record.storage_ref)
        profile = session.get(CredentialProfile, record.credential_profile_id)
        target = session.get(Target, record.target_id)
        if not profile or not target or profile.target_id != target.id or target.status != "active":
            raise InputValidationError("会话目标或身份已变更。")
        return parse_identity_state(json.dumps(payload.get("identity")), origin(target.base_url))

    def validate(self, session, session_id, before_request=lambda: None, *, current_state=None):
        now = datetime.now(timezone.utc)
        try:
            record = self._record(session, session_id)
            state = self.load_for_collection(session, session_id)
            if current_state is not None:
                state = parse_identity_state(current_state.model_dump_json(), state.target_origin)
            payload = self.storage.read_identity_payload(record.storage_ref)
            config = ManualLoginRequest.model_validate(payload["verification"])
            self.configuration(session, record.credential_profile_id, config)
            valid = self.verifier(state, config, before_request)
            record = self._record(session, session_id)
            record.last_validated_at = now
            record.status = "active" if valid else "invalid"
            record.last_error = None if valid else "restore_not_verified"
            session.flush()
            self._audit(session, "identity_validation", record, success=valid)
            return SessionHealthView(
                session_id=session_id,
                status="ready" if valid else "validation_failed",
                checked_at=now,
                reason_code=None if valid else "restore_not_verified",
            )
        except InputValidationError:
            return SessionHealthView(
                session_id=session_id,
                status="unknown",
                checked_at=now,
                reason_code="session_unavailable",
            )

    def revoke(self, session, session_id):
        record = session.get(AuthSession, session_id)
        if not record:
            raise InputValidationError("会话不存在。")
        record.revoked_at = datetime.now(timezone.utc)
        record.status = "invalid"
        session.flush()
        self._audit(session, "identity_revoke", record)

    def _audit(self, session, operation, record, success=True):
        from app.models.audit_event import AuditEvent

        session.add(
            AuditEvent(
                event_type=operation,
                status="success" if success else "failure",
                target_id=record.target_id,
                credential_profile_id=record.credential_profile_id,
                auth_session_id=record.id,
                detail_redacted={"operation": operation},
            )
        )
        session.flush()

    def activate_automatic_profile(self, session, profile_id):
        """Explicitly adapt an existing automatic login config using fresh positive proof."""
        from urllib.parse import urljoin, urlsplit

        from app.services.login_configs import LoginConfigService
        from app.services.sessions import AuthSessionService

        profile = session.get(CredentialProfile, profile_id)
        target = session.get(Target, profile.target_id) if profile else None
        if not target or target.status != "active":
            raise InputValidationError("身份或目标不可用。")
        try:
            config = LoginConfigService().load(profile.login_config_path)
            if config.adapter == "playwright":
                proof = ManualLoginRequest(
                    profile_id=profile_id,
                    login_url=urljoin(target.base_url, config.login_url),
                    validate_url=urljoin(target.base_url, config.validate_url),
                    success_selector=config.success_selector,
                    success_text=config.success_text,
                )
            else:
                proof = ManualLoginRequest(
                    profile_id=profile_id,
                    login_url=urljoin(target.base_url + "/", config.login_request.url),
                    validate_url=urljoin(target.base_url + "/", config.validate_request.url),
                    success_text=config.validate_request.success_contains,
                )
            self.configuration(session, profile_id, proof)
        except Exception:
            raise InputValidationError(
                "自动登录配置需要有效的正向成功条件；也可使用人工登录或导入。"
            ) from None
        legacy = AuthSessionService(storage_service=self.storage).ensure_valid_for_profile(
            session, profile_id
        )
        payload = self.storage.read_payload(legacy.storage_ref)
        if "storage_state" in payload:
            state = payload["storage_state"]
        elif isinstance(payload.get("cookies"), dict):
            state = {
                "cookies": [
                    {
                        "name": name,
                        "value": value,
                        "domain": urlsplit(target.base_url).hostname,
                        "path": "/",
                    }
                    for name, value in payload["cookies"].items()
                ],
                "origins": [],
            }
        else:
            raise InputValidationError("现有状态不能转换为浏览器状态，请使用人工登录或导入。")
        return self.import_state(session, profile_id, json.dumps(state), proof)
