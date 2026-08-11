"""认证覆盖运行的上下文建立行为测试。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import select

from app.core.errors import InputValidationError
from app.db.bootstrap import get_session
from app.models.audit_event import AuditEvent
from app.models.auth_session import AuthSession
from app.models.credential_profile import CredentialProfile
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanRunStage
from app.models.target import Target
from app.schemas.auth import LoginExecutionResult, SessionValidationResult
from app.services.context_establishment import ContextEstablishmentService
from app.services.login_configs import encode_inline_login_config
from app.services.scan_pipeline import ScanPipeline
from app.services.session_storage import SessionStorageService
from app.services.sessions import AuthSessionService
from tests.helpers.assessment import make_authenticated_run


@pytest.fixture
def db_session():
    session = get_session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@dataclass(frozen=True)
class Profiles:
    target_id: int
    user_id: int
    admin_id: int
    other_target_admin_id: int
    wrong_role_id: int


@pytest.fixture
def profiles(db_session) -> Profiles:
    target = Target(
        name="context-target",
        base_url="http://127.0.0.1:8080/app",
        type="web",
        owner="appsec",
        tags=[],
        status="active",
    )
    other_target = Target(
        name="other-context-target",
        base_url="http://127.0.0.1:9090/app",
        type="web",
        owner="appsec",
        tags=[],
        status="active",
    )
    db_session.add_all([target, other_target])
    db_session.flush()
    inline_config = encode_inline_login_config(
        {
            "adapter": "http",
            "login_request": {"method": "POST", "url": "/login"},
            "validate_request": {"method": "GET", "url": "/me"},
        }
    )

    def add_profile(*, target_id: int, name: str, role: str) -> CredentialProfile:
        profile = CredentialProfile(
            target_id=target_id,
            name=name,
            role=role,
            auth_type="password",
            username=f"{name}@example.local",
            secret_ref="literal://never-audited-secret",
            login_config_path=inline_config,
        )
        db_session.add(profile)
        db_session.flush()
        return profile

    user = add_profile(target_id=target.id, name="context-user", role="user")
    admin = add_profile(target_id=target.id, name="context-admin", role="admin")
    other_admin = add_profile(
        target_id=other_target.id,
        name="other-context-admin",
        role="admin",
    )
    wrong_role = add_profile(target_id=target.id, name="context-auditor", role="auditor")
    return Profiles(
        target_id=target.id,
        user_id=user.id,
        admin_id=admin.id,
        other_target_admin_id=other_admin.id,
        wrong_role_id=wrong_role.id,
    )


class SuccessfulAuthService:
    def ensure_valid_for_profile(self, session, profile_id: int) -> AuthSession:
        profile = session.get(CredentialProfile, profile_id)
        auth_session = AuthSession(
            target_id=profile.target_id,
            credential_profile_id=profile.id,
            status="active",
            session_type="http_cookie_jar",
            refresh_supported=False,
            session_metadata_redacted={"adapter": "test"},
            storage_ref=f"vault://session/{profile.id}",
        )
        session.add(auth_session)
        session.flush()
        return auth_session


class FailingAuthService(SuccessfulAuthService):
    def __init__(self, *failing_profile_ids: int) -> None:
        self.failing_profile_ids = set(failing_profile_ids)

    def ensure_valid_for_profile(self, session, profile_id: int) -> AuthSession:
        if profile_id in self.failing_profile_ids:
            raise InputValidationError("login rejected")
        return super().ensure_valid_for_profile(session, profile_id)


class RecordingHttpAdapter:
    def __init__(self) -> None:
        self.login_count = 0
        self.validated_payloads: list[dict[str, object]] = []

    def login(self, target, credential_profile, secret_value, config):
        self.login_count += 1
        assert secret_value == "never-audited-secret"
        return LoginExecutionResult(
            success=True,
            adapter="http",
            target_name=target.name,
            profile_name=credential_profile.name,
            status="active",
            session_type="http_cookie_jar",
            message="login succeeded",
            storage_payload={"cookies": {"session": "raw-cookie-value"}},
            session_metadata_redacted={"cookie_names": ["session"]},
        )

    def validate(self, target, credential_profile, config, stored_payload):
        self.validated_payloads.append(stored_payload)
        return SessionValidationResult(
            valid=True,
            status="active",
            message="session valid",
        )


def context_by_kind(contexts: list[ScanContext], kind: str) -> ScanContext:
    return next(context for context in contexts if context.kind == kind)


def make_profile_run(db_session, profiles: Profiles, admin_profile_id: int | None = None):
    run = make_authenticated_run(
        db_session,
        profiles.user_id,
        admin_profile_id or profiles.admin_id,
    )
    run.target_id = profiles.target_id
    run.input_url = "http://127.0.0.1:8080/start"
    run.normalized_url = run.input_url
    return run


def test_profiles_must_share_target_and_expected_roles(db_session, profiles):
    service = ContextEstablishmentService(auth_service=SuccessfulAuthService())
    run = make_profile_run(db_session, profiles, profiles.other_target_admin_id)

    with pytest.raises(InputValidationError, match="同一目标"):
        service.establish(db_session, run)

    role_run = make_profile_run(db_session, profiles, profiles.wrong_role_id)
    with pytest.raises(InputValidationError, match="admin"):
        service.establish(db_session, role_run)


def test_run_url_must_share_target_origin(db_session, profiles):
    run = make_profile_run(db_session, profiles)
    run.normalized_url = "http://127.0.0.1:8081/start"

    with pytest.raises(InputValidationError, match="同源"):
        ContextEstablishmentService(auth_service=SuccessfulAuthService()).establish(db_session, run)


def test_establish_updates_existing_contexts_and_writes_redacted_audit(db_session, profiles):
    run = make_profile_run(db_session, profiles)
    existing_ids = {context.kind: context.id for context in run.contexts}

    contexts = ContextEstablishmentService(auth_service=SuccessfulAuthService()).establish(
        db_session, run
    )
    db_session.flush()

    assert {context.kind: context.id for context in contexts} == existing_ids
    assert len(db_session.scalars(select(ScanContext)).all()) == 3
    anonymous = context_by_kind(contexts, "anonymous")
    assert (anonymous.status, anonymous.login_status) == ("ready", "not_applicable")
    for kind in ("user", "admin"):
        context = context_by_kind(contexts, kind)
        assert context.status == "ready"
        assert context.login_status == "succeeded"
        assert context.session_validation_status == "valid"
        assert context.auth_session_id is not None

    events = list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "context_establishment")
        )
    )
    assert len(events) == 3
    assert {event.detail_redacted["context_kind"] for event in events} == {
        "anonymous",
        "user",
        "admin",
    }
    serialized = repr([event.detail_redacted for event in events]).lower()
    assert "secret" not in serialized
    assert "cookie" not in serialized
    assert "authorization" not in serialized
    assert "payload" not in serialized


@pytest.mark.parametrize(
    ("failed_kind", "expected_completeness"),
    [
        ("user", "missing_user_context"),
        ("admin", "missing_admin_context"),
    ],
)
def test_single_login_failure_marks_run_incomplete(
    db_session, profiles, failed_kind, expected_completeness
):
    run = make_profile_run(db_session, profiles)
    run.active_key = "context-establishment-active-key"
    failed_profile_id = getattr(profiles, f"{failed_kind}_id")
    service = ContextEstablishmentService(auth_service=FailingAuthService(failed_profile_id))

    contexts = service.establish(db_session, run)

    failed = context_by_kind(contexts, failed_kind)
    assert failed.status == "failed"
    assert failed.login_status == "failed"
    assert failed.failure_count == 1
    assert run.status == "incomplete"
    assert run.completeness == expected_completeness
    assert run.error_code == "context_establishment_failed"
    assert run.active_key is None


def test_both_login_failures_use_general_incomplete_completeness(db_session, profiles):
    run = make_profile_run(db_session, profiles)
    service = ContextEstablishmentService(
        auth_service=FailingAuthService(profiles.user_id, profiles.admin_id)
    )

    contexts = service.establish(db_session, run)

    assert context_by_kind(contexts, "user").status == "failed"
    assert context_by_kind(contexts, "admin").status == "failed"
    assert run.status == "incomplete"
    assert run.completeness == "incomplete"


def test_pipeline_runs_only_context_stage_for_authenticated_run(db_session, profiles):
    run = make_profile_run(db_session, profiles)
    stage = ScanRunStage(
        scan_run_id=run.id,
        name="establish_contexts",
        position=2,
        status="pending",
    )
    db_session.add(stage)
    db_session.flush()
    pipeline = ScanPipeline(
        policy=object(),
        gateway=object(),
        context_service=ContextEstablishmentService(auth_service=SuccessfulAuthService()),
    )

    contexts = pipeline.establish_contexts(db_session, run.id)

    assert [context.kind for context in contexts] == ["anonymous", "user", "admin"]
    assert stage.status == "completed"
    assert stage.attempt == 1
    assert run.status == "queued"
    assert run.current_stage == "establish_contexts"
    assert run.progress > 0


def test_ensure_valid_for_profile_reuses_storage_backed_session(db_session, profiles, tmp_path):
    adapter = RecordingHttpAdapter()
    service = AuthSessionService(
        storage_service=SessionStorageService(tmp_path / "session-payloads"),
        http_adapter=adapter,
    )

    created = service.ensure_valid_for_profile(db_session, profiles.user_id)
    reused = service.ensure_valid_for_profile(db_session, profiles.user_id)

    assert reused.id == created.id
    assert adapter.login_count == 1
    assert adapter.validated_payloads == [
        {"cookies": {"session": "raw-cookie-value"}},
        {"cookies": {"session": "raw-cookie-value"}},
    ]
    details = repr(db_session.scalars(select(AuditEvent.detail_redacted)).all()).lower()
    assert "raw-cookie-value" not in details
