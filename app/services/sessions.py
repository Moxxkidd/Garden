"""Session orchestration and lifecycle services."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ResourceNotFoundError
from app.integrations.auth.http_adapter import HttpLoginAdapter
from app.integrations.auth.playwright_adapter import PlaywrightLoginAdapter
from app.models.auth_session import AuthSession
from app.models.credential_profile import CredentialProfile
from app.models.enums import (
    AuditEventStatus,
    AuditEventType,
)
from app.redaction.display import redact_session_metadata
from app.schemas.auth import (
    HttpLoginConfig,
    LoginConfig,
    LoginExecutionResult,
    PlaywrightLoginConfig,
)
from app.services.audit import AuditService
from app.services.credentials import CredentialProfileService
from app.services.login_configs import LoginConfigService
from app.services.secret_resolver import SecretResolver
from app.services.session_storage import SessionStorageService
from app.services.targets import TargetService


class AuthSessionService:
    """Login orchestration, session persistence, validation, and refresh."""

    def __init__(
        self,
        *,
        target_service: TargetService | None = None,
        credential_service: CredentialProfileService | None = None,
        login_config_service: LoginConfigService | None = None,
        secret_resolver: SecretResolver | None = None,
        storage_service: SessionStorageService | None = None,
        audit_service: AuditService | None = None,
        http_adapter: HttpLoginAdapter | None = None,
        playwright_adapter: PlaywrightLoginAdapter | None = None,
    ) -> None:
        self.target_service = target_service or TargetService()
        self.credential_service = credential_service or CredentialProfileService()
        self.login_config_service = login_config_service or LoginConfigService()
        self.secret_resolver = secret_resolver or SecretResolver()
        self.storage_service = storage_service or SessionStorageService()
        self.audit_service = audit_service or AuditService()
        self.http_adapter = http_adapter or HttpLoginAdapter()
        self.playwright_adapter = playwright_adapter or PlaywrightLoginAdapter()

    def login_test(
        self, session: Session, target_name: str, profile_name: str
    ) -> LoginExecutionResult:
        target = self.target_service.get_by_name(session, target_name)
        credential = self.credential_service.get_by_target_and_name(
            session, target.id, profile_name
        )
        config = self.login_config_service.load(credential.login_config_path)
        secret_value = self.secret_resolver.resolve(credential.secret_ref)
        adapter = self._get_adapter(config)
        result = adapter.login(target, credential, secret_value, config)
        auth_session = None
        if result.success:
            auth_session = self._persist_session(
                session,
                target.id,
                credential.id,
                result,
            )
            result.created_session_id = auth_session.id
            result.message = f"{result.message} Session #{auth_session.id} created."
        self.audit_service.record(
            session,
            AuditEventType.LOGIN_ATTEMPT,
            AuditEventStatus.SUCCESS if result.success else AuditEventStatus.FAILURE,
            {
                "target": target.name,
                "profile": credential.name,
                "adapter": result.adapter,
                "status": result.status.value,
                "failure_reason": result.failure_reason.value if result.failure_reason else None,
                "message": result.message,
            },
            target_id=target.id,
            credential_profile_id=credential.id,
            auth_session_id=auth_session.id if auth_session else None,
        )
        return result

    def list(self, session: Session) -> list[AuthSession]:
        return list(session.scalars(select(AuthSession).order_by(AuthSession.id.desc())))

    def get(self, session: Session, session_id: int) -> AuthSession:
        auth_session = session.get(AuthSession, session_id)
        if auth_session is None:
            raise ResourceNotFoundError(f"Session {session_id} was not found.")
        return auth_session

    def list_for_target(self, session: Session, target_id: int) -> list[AuthSession]:
        return list(
            session.scalars(
                select(AuthSession)
                .where(AuthSession.target_id == target_id)
                .order_by(AuthSession.id.desc())
            )
        )

    def list_for_credential(
        self, session: Session, credential_profile_id: int
    ) -> list[AuthSession]:
        return list(
            session.scalars(
                select(AuthSession)
                .where(AuthSession.credential_profile_id == credential_profile_id)
                .order_by(AuthSession.id.desc())
            )
        )

    def validate(self, session: Session, session_id: int):
        auth_session = self.get(session, session_id)
        credential = session.get(CredentialProfile, auth_session.credential_profile_id)
        target = self.target_service.get(session, auth_session.target_id)
        if credential is None:
            raise ResourceNotFoundError(
                f"Credential profile {auth_session.credential_profile_id} was not found."
            )
        config = self.login_config_service.load(credential.login_config_path)
        payload = self.storage_service.read_payload(auth_session.storage_ref)
        adapter = self._get_adapter(config)
        result = adapter.validate(target, credential, config, payload)
        auth_session.status = result.status.value
        auth_session.last_validated_at = datetime.now(timezone.utc)
        auth_session.expires_at = result.expires_at or auth_session.expires_at
        if result.session_metadata_redacted:
            auth_session.session_metadata_redacted = redact_session_metadata(
                {
                    **auth_session.session_metadata_redacted,
                    **result.session_metadata_redacted,
                }
            )
        auth_session.last_error = result.last_error
        self.audit_service.record(
            session,
            AuditEventType.SESSION_VALIDATION,
            AuditEventStatus.SUCCESS if result.valid else AuditEventStatus.FAILURE,
            {
                "session_id": auth_session.id,
                "status": auth_session.status,
                "message": result.message,
                "failure_reason": result.failure_reason.value if result.failure_reason else None,
            },
            target_id=auth_session.target_id,
            credential_profile_id=auth_session.credential_profile_id,
            auth_session_id=auth_session.id,
            scan_job_id=auth_session.scan_job_id,
        )
        return result

    def refresh(self, session: Session, session_id: int) -> LoginExecutionResult:
        auth_session = self.get(session, session_id)
        if not auth_session.refresh_supported:
            raise ConflictError(f"Session {session_id} does not support refresh.")
        credential = session.get(CredentialProfile, auth_session.credential_profile_id)
        target = self.target_service.get(session, auth_session.target_id)
        if credential is None:
            raise ResourceNotFoundError(
                f"Credential profile {auth_session.credential_profile_id} was not found."
            )
        config = self.login_config_service.load(credential.login_config_path)
        payload = self.storage_service.read_payload(auth_session.storage_ref)
        secret_value = self.secret_resolver.resolve(credential.secret_ref)
        adapter = self._get_adapter(config)
        result = adapter.refresh(target, credential, secret_value, config, payload)
        if result.success:
            auth_session.status = result.status.value
            auth_session.expires_at = result.expires_at
            auth_session.session_type = (
                result.session_type.value if result.session_type else auth_session.session_type
            )
            auth_session.session_metadata_redacted = redact_session_metadata(
                {**auth_session.session_metadata_redacted, **result.session_metadata_redacted}
            )
            auth_session.storage_ref = self.storage_service.write_payload(
                auth_session.id, result.storage_payload
            )
            auth_session.last_error = None
        else:
            auth_session.status = result.status.value
            auth_session.last_error = result.last_error or result.message
        self.audit_service.record(
            session,
            AuditEventType.SESSION_REFRESH,
            AuditEventStatus.SUCCESS if result.success else AuditEventStatus.FAILURE,
            {
                "session_id": auth_session.id,
                "status": auth_session.status,
                "message": result.message,
                "failure_reason": result.failure_reason.value if result.failure_reason else None,
            },
            target_id=auth_session.target_id,
            credential_profile_id=auth_session.credential_profile_id,
            auth_session_id=auth_session.id,
            scan_job_id=auth_session.scan_job_id,
        )
        return result

    def _persist_session(
        self,
        session: Session,
        target_id: int,
        credential_profile_id: int,
        result: LoginExecutionResult,
    ) -> AuthSession:
        auth_session = AuthSession(
            target_id=target_id,
            credential_profile_id=credential_profile_id,
            status=result.status.value,
            session_type=result.session_type.value if result.session_type else "unknown",
            expires_at=result.expires_at,
            refresh_supported=result.refresh_supported,
            session_metadata_redacted=redact_session_metadata(result.session_metadata_redacted),
            storage_ref="",
            last_error=result.last_error,
        )
        session.add(auth_session)
        session.flush()
        auth_session.storage_ref = self.storage_service.write_payload(
            auth_session.id, result.storage_payload
        )
        session.flush()
        session.refresh(auth_session)
        return auth_session

    def _get_adapter(self, config: LoginConfig):
        if isinstance(config, HttpLoginConfig):
            return self.http_adapter
        if isinstance(config, PlaywrightLoginConfig):
            return self.playwright_adapter
        raise ResourceNotFoundError("No adapter was available for the provided login config.")
