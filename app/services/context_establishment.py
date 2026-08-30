"""建立统一验证运行的匿名、用户和管理员上下文。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli.paths import formal_runtime_paths
from app.core.errors import InputValidationError, ResourceNotFoundError
from app.models.credential_profile import CredentialProfile
from app.models.enums import (
    AssessmentMode,
    AuditEventStatus,
    AuditEventType,
    CompletenessStatus,
    ContextKind,
)
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanRun
from app.models.target import Target
from app.services.audit import AuditService
from app.services.ephemeral_secret_store import EphemeralSecretStore
from app.services.secret_resolver import SecretResolver
from app.services.sessions import AuthSessionService


class ContextEstablishmentService:
    """Validate identities and bind verified auth sessions to persisted contexts."""

    def __init__(
        self,
        *,
        auth_service: AuthSessionService | None = None,
        audit_service: AuditService | None = None,
        ephemeral_store: EphemeralSecretStore | None = None,
    ) -> None:
        if ephemeral_store is None:
            formal_paths = formal_runtime_paths()
            ephemeral_store = EphemeralSecretStore(
                formal_paths.secrets_dir
                if formal_paths is not None
                else Path.cwd() / "data" / "ephemeral-secrets"
            )
        if auth_service is not None:
            self.auth_service = auth_service
        else:
            self.auth_service = AuthSessionService(
                secret_resolver=SecretResolver(ephemeral_store=ephemeral_store)
            )
        self.audit_service = audit_service or AuditService()
        self.ephemeral_store = ephemeral_store

    def establish(
        self,
        session: Session,
        run: ScanRun,
        *,
        before_request: Callable[[], None] | None = None,
    ) -> list[ScanContext]:
        contexts = self._existing_contexts(session, run)
        anonymous = self._establish_anonymous(session, run, contexts)
        if run.mode == AssessmentMode.QUICK.value:
            return [anonymous]

        try:
            user_profile, admin_profile = self._validate_authenticated_run(session, run, contexts)
            user = self._establish_authenticated(
                session,
                run,
                contexts[ContextKind.USER.value],
                user_profile,
                before_request=before_request,
            )
            admin = self._establish_authenticated(
                session,
                run,
                contexts[ContextKind.ADMIN.value],
                admin_profile,
                before_request=before_request,
            )
            self._set_run_completeness(run, user, admin)
            session.flush()
            return [anonymous, user, admin]
        finally:
            self._delete_context_temporary_secrets(session, contexts)

    def _delete_context_temporary_secrets(
        self,
        session: Session,
        contexts: dict[str, ScanContext],
    ) -> None:
        if self.ephemeral_store is None:
            return
        for kind in (ContextKind.USER.value, ContextKind.ADMIN.value):
            profile_id = contexts[kind].credential_profile_id
            profile = session.get(CredentialProfile, profile_id) if profile_id is not None else None
            if profile is not None and profile.secret_ref.startswith("ephemeral-file://"):
                self.ephemeral_store.delete(profile.secret_ref)

    def _existing_contexts(self, session: Session, run: ScanRun) -> dict[str, ScanContext]:
        contexts = {
            context.kind: context
            for context in session.scalars(
                select(ScanContext)
                .where(ScanContext.scan_run_id == run.id)
                .order_by(ScanContext.id)
            )
        }
        required = {ContextKind.ANONYMOUS.value}
        if run.mode == AssessmentMode.AUTHENTICATED_COVERAGE.value:
            required.update({ContextKind.USER.value, ContextKind.ADMIN.value})
        missing = sorted(required - contexts.keys())
        if missing:
            raise InputValidationError(f"验证运行缺少预创建上下文：{', '.join(missing)}。")
        return contexts

    def _validate_authenticated_run(
        self,
        session: Session,
        run: ScanRun,
        contexts: dict[str, ScanContext],
    ) -> tuple[CredentialProfile, CredentialProfile]:
        user = self._profile(session, contexts[ContextKind.USER.value])
        admin = self._profile(session, contexts[ContextKind.ADMIN.value])
        if user.target_id != admin.target_id:
            raise InputValidationError("user/admin 凭证档案必须属于同一目标。")
        if user.role != ContextKind.USER.value:
            raise InputValidationError("user 上下文必须绑定角色精确为 user 的凭证档案。")
        if admin.role != ContextKind.ADMIN.value:
            raise InputValidationError("admin 上下文必须绑定角色精确为 admin 的凭证档案。")
        if run.target_id != user.target_id:
            raise InputValidationError("验证运行 target 与凭证档案 target 不一致。")
        target = session.get(Target, user.target_id)
        if target is None:
            raise ResourceNotFoundError(f"Target {user.target_id} was not found.")
        if self._origin(run.normalized_url) != self._origin(target.base_url):
            raise InputValidationError("验证运行 URL 必须与 target base URL 同源。")
        return user, admin

    def _profile(self, session: Session, context: ScanContext) -> CredentialProfile:
        if context.credential_profile_id is None:
            raise InputValidationError(f"{context.kind} 上下文缺少凭证档案。")
        profile = session.get(CredentialProfile, context.credential_profile_id)
        if profile is None:
            raise ResourceNotFoundError(
                f"Credential profile {context.credential_profile_id} was not found."
            )
        return profile

    def _establish_anonymous(
        self,
        session: Session,
        run: ScanRun,
        contexts: dict[str, ScanContext],
    ) -> ScanContext:
        context = contexts[ContextKind.ANONYMOUS.value]
        now = datetime.now(timezone.utc)
        context.status = "ready"
        context.login_status = "not_applicable"
        context.session_validation_status = "not_applicable"
        context.started_at = context.started_at or now
        context.finished_at = now
        context.error_code = None
        context.error_message = None
        self._audit(session, run, context, succeeded=True)
        return context

    def _establish_authenticated(
        self,
        session: Session,
        run: ScanRun,
        context: ScanContext,
        profile: CredentialProfile,
        *,
        before_request: Callable[[], None] | None,
    ) -> ScanContext:
        now = datetime.now(timezone.utc)
        context.started_at = context.started_at or now
        try:
            with session.begin_nested():
                if before_request is None:
                    auth_session = self.auth_service.ensure_valid_for_profile(
                        session,
                        profile.id,
                    )
                else:
                    auth_session = self.auth_service.ensure_valid_for_profile(
                        session,
                        profile.id,
                        before_request=before_request,
                    )
        except Exception:  # noqa: BLE001 - context boundary persists a redacted failure
            context.auth_session_id = None
            context.status = "failed"
            context.login_status = "failed"
            context.session_validation_status = "invalid"
            context.failure_count += 1
            context.error_code = "authentication_session_unavailable"
            context.error_message = "Authentication session could not be established and validated."
            context.finished_at = now
            self._audit(session, run, context, succeeded=False)
            return context

        if (
            auth_session.target_id != run.target_id
            or auth_session.credential_profile_id != profile.id
        ):
            context.auth_session_id = None
            context.status = "failed"
            context.login_status = "failed"
            context.session_validation_status = "invalid"
            context.failure_count += 1
            context.error_code = "authentication_session_mismatch"
            context.error_message = "Validated session ownership did not match the context."
            context.finished_at = now
            self._audit(session, run, context, succeeded=False)
            return context

        context.auth_session_id = auth_session.id
        context.status = "ready"
        context.login_status = "succeeded"
        context.session_validation_status = "valid"
        context.error_code = None
        context.error_message = None
        context.finished_at = now
        self._audit(session, run, context, succeeded=True)
        return context

    def _set_run_completeness(self, run: ScanRun, user: ScanContext, admin: ScanContext) -> None:
        failed = {context.kind for context in (user, admin) if context.status == "failed"}
        if not failed:
            return
        run.status = "incomplete"
        if failed == {ContextKind.USER.value, ContextKind.ADMIN.value}:
            run.completeness = CompletenessStatus.INCOMPLETE.value
        elif ContextKind.ADMIN.value in failed:
            run.completeness = CompletenessStatus.MISSING_ADMIN_CONTEXT.value
        else:
            run.completeness = CompletenessStatus.MISSING_USER_CONTEXT.value
        run.error_code = "context_establishment_failed"
        run.error_message = "One or more required authentication contexts could not be established."
        run.finished_at = datetime.now(timezone.utc)

    def _audit(
        self,
        session: Session,
        run: ScanRun,
        context: ScanContext,
        *,
        succeeded: bool,
    ) -> None:
        self.audit_service.record(
            session,
            AuditEventType.CONTEXT_ESTABLISHMENT,
            AuditEventStatus.SUCCESS if succeeded else AuditEventStatus.FAILURE,
            {
                "scan_run_id": run.id,
                "context_kind": context.kind,
                "credential_profile_id": context.credential_profile_id,
                "auth_session_id": context.auth_session_id,
                "result": "success" if succeeded else "failure",
            },
            target_id=run.target_id,
            credential_profile_id=context.credential_profile_id,
            auth_session_id=context.auth_session_id,
        )

    def _origin(self, url: str) -> tuple[str, str, int]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise InputValidationError("验证运行与 target URL 必须是有效 HTTP(S) URL。")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as error:
            raise InputValidationError("验证运行与 target URL 端口无效。") from error
        return parsed.scheme.lower(), parsed.hostname.lower(), port
