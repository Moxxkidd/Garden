import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import select

from app.cli.coverage_wizard import CoverageSetupWizard
from app.core.errors import ConflictError, InputValidationError
from app.db.bootstrap import session_scope
from app.models.credential_profile import CredentialProfile
from app.models.enums import AuthType, TargetType
from app.models.target import Target
from app.schemas.credential import CredentialProfileCreate
from app.schemas.target import TargetCreate
from app.services.credentials import CredentialProfileService
from app.services.ephemeral_secret_store import EphemeralSecretStore
from app.services.login_configs import LoginConfigService, encode_inline_login_config
from app.services.targets import TargetService


class ScriptedPrompts:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.hidden_values: list[str] = []
        self.rendered_text = ""
        self.offered: dict[str, tuple[str, ...]] = {}
        self.offered_labels: dict[str, tuple[str, ...]] = {}

    def choose(self, label: str, choices) -> str:
        values = tuple(choice.value for choice in choices)
        self.offered[label] = values
        self.offered_labels[label] = tuple(choice.label for choice in choices)
        answer = self.answers.pop(0)
        if label == "确认提交或返回调整":
            answer = {"yes": "submit", "no": "cancel"}.get(answer, answer)
        assert answer in values
        return answer

    def confirm(self, label: str, *, default: bool = True) -> bool:
        answer = self.answers.pop(0).lower()
        return answer in {"yes", "y", "是"}

    def text(self, label: str, *, default: str | None = None) -> str:
        answer = self.answers.pop(0)
        return answer or default or ""

    def secret(self, label: str) -> str:
        answer = self.answers.pop(0)
        self.hidden_values.append(answer)
        return answer

    def write(self, text: str) -> None:
        self.rendered_text += text


@dataclass(frozen=True)
class SetupRecords:
    url: str
    target_id: int
    user_id: int
    admin_id: int
    wrong_role_id: int


def _create_profile(session, target_id: int, *, name: str, role: str):
    login_config = encode_inline_login_config(
        {
            "adapter": "http",
            "login_request": {"method": "POST", "url": "/login"},
            "validate_request": {"method": "GET", "url": "/me"},
        }
    )
    return CredentialProfileService().create(
        session,
        CredentialProfileCreate(
            target_id=target_id,
            name=name,
            role=role,
            auth_type=AuthType.PASSWORD,
            username=f"{name}@example.test",
            secret_ref=f"env://{name.upper().replace('-', '_')}_SECRET",
            login_config_path=login_config,
        ),
    )


def _setup_records() -> SetupRecords:
    with session_scope() as session:
        target = TargetService().create(
            session,
            TargetCreate(
                name="coverage-app",
                base_url="http://127.0.0.1:8181/app",
                type=TargetType.WEB,
                owner="appsec",
            ),
        )
        other = TargetService().create(
            session,
            TargetCreate(
                name="other-app",
                base_url="http://127.0.0.1:8282/app",
                type=TargetType.WEB,
                owner="appsec",
            ),
        )
        user = _create_profile(session, target.id, name="coverage-user", role="user")
        admin = _create_profile(session, target.id, name="coverage-admin", role="admin")
        wrong_role = _create_profile(session, target.id, name="coverage-auditor", role="auditor")
        _create_profile(session, other.id, name="other-user", role="user")
        return SetupRecords(
            url="http://127.0.0.1:8181/start",
            target_id=target.id,
            user_id=user.id,
            admin_id=admin.id,
            wrong_role_id=wrong_role.id,
        )


def test_wizard_selects_same_origin_target_and_exact_roles() -> None:
    setup = _setup_records()
    prompts = ScriptedPrompts(
        [str(setup.target_id), str(setup.user_id), str(setup.admin_id), "yes"]
    )

    request = CoverageSetupWizard(prompts=prompts).run(setup.url)

    assert request.target_id == setup.target_id
    assert request.user_profile_id == setup.user_id
    assert request.admin_profile_id == setup.admin_id
    assert request.active_checks_enabled is False
    assert prompts.hidden_values == []
    assert prompts.offered["选择同源 Target"] == (str(setup.target_id),)
    assert "appsec" in prompts.offered_labels["选择同源 Target"][0]
    assert prompts.offered["选择 user 凭据档案"] == (str(setup.user_id), "__create__")
    assert prompts.offered["选择 admin 凭据档案"] == (str(setup.admin_id), "__create__")


def test_wizard_can_create_a_new_profile_when_existing_profiles_are_available(
    tmp_path: Path,
) -> None:
    setup = _setup_records()
    prompts = ScriptedPrompts(
        [
            str(setup.target_id),
            "__create__",
            "alternate-coverage-user",
            "/login",
            "/account",
            "alternate@example.test",
            "temporary-user-secret",
            str(setup.admin_id),
            "yes",
        ]
    )

    request = CoverageSetupWizard(
        prompts=prompts,
        secret_store=EphemeralSecretStore(tmp_path / "secrets"),
    ).run(setup.url)

    assert request.user_profile_id != setup.user_id
    assert request.admin_profile_id == setup.admin_id
    assert prompts.offered["选择 user 凭据档案"] == (
        str(setup.user_id),
        "__create__",
    )


def test_wizard_reprompts_for_an_expired_temporary_profile_secret(
    tmp_path: Path,
) -> None:
    setup = _setup_records()
    store = EphemeralSecretStore(tmp_path / "secrets")
    expired_reference = store.write("expired-secret")
    store.delete(expired_reference)
    with session_scope() as session:
        session.get(CredentialProfile, setup.user_id).secret_ref = expired_reference
        session.commit()
    prompts = ScriptedPrompts(
        [
            str(setup.target_id),
            str(setup.user_id),
            str(setup.admin_id),
            "yes",
            "replacement-user-secret",
        ]
    )

    request = CoverageSetupWizard(prompts=prompts, secret_store=store).run(setup.url)

    assert request.user_profile_id == setup.user_id
    assert prompts.hidden_values == ["replacement-user-secret"]
    with session_scope() as session:
        profile = session.get(CredentialProfile, setup.user_id)
        assert profile.secret_ref != expired_reference
        assert store.read(profile.secret_ref) == "replacement-user-secret"


def test_wizard_reuses_a_valid_protected_session_before_reprompting_for_secret(
    tmp_path: Path,
) -> None:
    setup = _setup_records()
    store = EphemeralSecretStore(tmp_path / "secrets")
    consumed_reference = store.write("consumed-secret")
    store.delete(consumed_reference)
    with session_scope() as session:
        session.get(CredentialProfile, setup.user_id).secret_ref = consumed_reference
        session.commit()

    class ReusableSessionService:
        def __init__(self) -> None:
            self.profile_ids: list[int] = []

        def ensure_valid_for_profile(self, session, profile_id):
            self.profile_ids.append(profile_id)
            return object()

    auth_service = ReusableSessionService()
    prompts = ScriptedPrompts(
        [str(setup.target_id), str(setup.user_id), str(setup.admin_id), "yes"]
    )

    request = CoverageSetupWizard(
        prompts=prompts,
        secret_store=store,
        auth_session_service=auth_service,
    ).run(setup.url)

    assert request.user_profile_id == setup.user_id
    assert auth_service.profile_ids == [setup.user_id]
    assert prompts.hidden_values == []


def test_wizard_rejects_a_selected_profile_with_invalid_login_config() -> None:
    setup = _setup_records()
    with session_scope() as session:
        session.get(CredentialProfile, setup.user_id).login_config_path = "inline://broken"
        session.commit()
    prompts = ScriptedPrompts(
        [str(setup.target_id), str(setup.user_id), str(setup.admin_id), "cancel"]
    )

    with pytest.raises(InputValidationError, match="已取消"):
        CoverageSetupWizard(prompts=prompts).run(setup.url)

    assert prompts.hidden_values == []


def test_wizard_rejects_cross_origin_urls_in_a_selected_login_config() -> None:
    setup = _setup_records()
    cross_origin_config = encode_inline_login_config(
        {
            "adapter": "playwright",
            "login_url": "https://outside.example/login",
            "validate_url": "/me",
            "auto_detect_selectors": True,
        }
    )
    with session_scope() as session:
        session.get(CredentialProfile, setup.user_id).login_config_path = cross_origin_config
        session.commit()
    prompts = ScriptedPrompts(
        [str(setup.target_id), str(setup.user_id), str(setup.admin_id), "cancel"]
    )

    with pytest.raises(InputValidationError, match="已取消"):
        CoverageSetupWizard(prompts=prompts).run(setup.url)

    assert prompts.hidden_values == []


def test_wizard_creates_same_origin_target_and_missing_profiles_without_exposing_secrets(
    tmp_path: Path,
) -> None:
    user_secret = "user-entered-password"
    admin_secret = "admin-entered-password"
    prompts = ScriptedPrompts(
        [
            "yes",
            "coverage-app",
            "appsec",
            "yes",
            "coverage-user",
            "/login",
            "/account",
            "reader@example.test",
            user_secret,
            "yes",
            "coverage-admin",
            "/login",
            "/admin",
            "admin@example.test",
            admin_secret,
            "yes",
        ]
    )
    secret_store = EphemeralSecretStore(tmp_path / "secrets")

    wizard = CoverageSetupWizard(
        prompts=prompts,
        secret_store=secret_store,
    )
    request = wizard.run("http://127.0.0.1:8181/start")

    with session_scope() as session:
        created_user = session.get(CredentialProfile, request.user_profile_id)
        created_admin = session.get(CredentialProfile, request.admin_profile_id)
        assert created_user is not None
        assert created_admin is not None
        assert created_user.target_id == request.target_id == created_admin.target_id
        assert (created_user.role, created_admin.role) == ("user", "admin")
        assert created_user.secret_ref.startswith("ephemeral-file://")
        assert created_admin.secret_ref.startswith("ephemeral-file://")
        user_login_config = LoginConfigService().load(created_user.login_config_path)
        admin_login_config = LoginConfigService().load(created_admin.login_config_path)
        serialized = json.dumps(
            {"user": created_user.__dict__, "admin": created_admin.__dict__},
            default=str,
        )

    assert prompts.hidden_values == [user_secret, admin_secret]
    assert secret_store.read(created_user.secret_ref) == user_secret
    assert secret_store.read(created_admin.secret_ref) == admin_secret
    assert user_login_config.adapter == admin_login_config.adapter == "playwright"
    assert user_login_config.auto_detect_selectors is True
    assert admin_login_config.auto_detect_selectors is True
    assert user_secret not in serialized
    assert admin_secret not in serialized
    assert user_secret not in prompts.rendered_text
    assert admin_secret not in prompts.rendered_text

    wizard.cleanup_unsubmitted_secrets()

    assert list(secret_store.root.glob("*.secret")) == []


def test_wizard_cancellation_rolls_back_draft_and_deletes_temporary_secrets(
    tmp_path: Path,
) -> None:
    prompts = ScriptedPrompts(
        [
            "yes",
            "cancelled-target",
            "appsec",
            "yes",
            "cancelled-user",
            "/login",
            "/account",
            "reader@example.test",
            "temporary-user-secret",
            "yes",
            "cancelled-admin",
            "/login",
            "/admin",
            "admin@example.test",
            "temporary-admin-secret",
            "no",
        ]
    )
    secret_store = EphemeralSecretStore(tmp_path / "secrets")

    with pytest.raises(InputValidationError, match="已取消"):
        CoverageSetupWizard(prompts=prompts, secret_store=secret_store).run(
            "http://127.0.0.1:8181/start"
        )

    assert list(secret_store.root.glob("*.secret")) == []
    with session_scope() as session:
        assert list(session.scalars(select(Target))) == []
        assert list(session.scalars(select(CredentialProfile))) == []


def test_wizard_keyboard_interrupt_rolls_back_and_deletes_draft_secrets(
    tmp_path: Path,
) -> None:
    class InterruptingPrompts(ScriptedPrompts):
        def secret(self, label: str) -> str:
            if self.hidden_values:
                raise KeyboardInterrupt
            return super().secret(label)

    prompts = InterruptingPrompts(
        [
            "yes",
            "interrupted-target",
            "appsec",
            "yes",
            "interrupted-user",
            "/login",
            "/account",
            "reader@example.test",
            "temporary-user-secret",
            "yes",
            "interrupted-admin",
            "/login",
            "/admin",
            "admin@example.test",
        ]
    )
    secret_store = EphemeralSecretStore(tmp_path / "secrets")

    with pytest.raises(KeyboardInterrupt):
        CoverageSetupWizard(prompts=prompts, secret_store=secret_store).run(
            "http://127.0.0.1:8181/start"
        )

    assert list(secret_store.root.glob("*.secret")) == []
    with session_scope() as session:
        assert list(session.scalars(select(Target))) == []
        assert list(session.scalars(select(CredentialProfile))) == []


def test_wizard_profile_creation_failure_rolls_back_and_deletes_all_draft_secrets(
    tmp_path: Path,
) -> None:
    with session_scope() as session:
        target = TargetService().create(
            session,
            TargetCreate(
                name="coverage-app",
                base_url="http://127.0.0.1:8181",
                type=TargetType.WEB,
                owner="appsec",
            ),
        )
        existing = _create_profile(session, target.id, name="collision", role="auditor")
        target_id = target.id
        existing_id = existing.id

    prompts = ScriptedPrompts(
        [
            str(target_id),
            "yes",
            "draft-user",
            "/login",
            "/account",
            "reader@example.test",
            "temporary-user-secret",
            "yes",
            "collision",
            "/login",
            "/admin",
            "admin@example.test",
            "temporary-admin-secret",
        ]
    )
    secret_store = EphemeralSecretStore(tmp_path / "secrets")

    with pytest.raises(ConflictError, match="already exists"):
        CoverageSetupWizard(prompts=prompts, secret_store=secret_store).run(
            "http://127.0.0.1:8181/start"
        )

    assert list(secret_store.root.glob("*.secret")) == []
    with session_scope() as session:
        profiles = list(session.scalars(select(CredentialProfile)))
        assert [(profile.id, profile.name, profile.role) for profile in profiles] == [
            (existing_id, "collision", "auditor")
        ]


def test_preview_shows_effective_options_and_allows_adjustment() -> None:
    from app.schemas.scan import ScanOptions

    setup = _setup_records()
    _create_source_run(setup.url, setup.target_id)
    prompts = ScriptedPrompts(
        [
            str(setup.target_id),
            str(setup.user_id),
            str(setup.admin_id),
            "edit_options",
            "9",
            "40",
            "1",
            "4",
            "90",
            "0",
            "submit",
        ]
    )
    request = CoverageSetupWizard(prompts=prompts).run(
        setup.url + "?token=private-preview-value",
        options=ScanOptions(max_pages=7),
        source_run_id=17,
    )
    assert request.options.max_pages == 9
    assert request.options.retry_attempts == 0
    assert request.source_run_id == 17
    assert "页面上限: 7" in prompts.rendered_text
    assert "页面上限: 9" in prompts.rendered_text
    assert "private-preview-value" not in prompts.rendered_text
    assert "尚未验证" in prompts.rendered_text


def test_wizard_defers_session_network_until_final_confirmation(tmp_path: Path) -> None:
    setup = _setup_records()
    store = EphemeralSecretStore(tmp_path / "secrets")
    reference = store.write("expired")
    store.delete(reference)
    with session_scope() as session:
        session.get(CredentialProfile, setup.user_id).secret_ref = reference
    events = []

    class Prompts(ScriptedPrompts):
        def choose(self, label, choices):
            answer = super().choose(label, choices)
            if answer == "submit":
                events.append("confirmed")
            return answer

    class Sessions:
        def ensure_valid_for_profile(self, session, profile_id):
            events.append("network")
            assert events[0] == "confirmed"
            return object()

    prompts = Prompts([str(setup.target_id), str(setup.user_id), str(setup.admin_id), "submit"])
    CoverageSetupWizard(prompts=prompts, secret_store=store, auth_session_service=Sessions()).run(
        setup.url
    )
    assert events == ["confirmed", "network"]
    assert prompts.hidden_values == []


def test_cleanup_after_runtime_failure_removes_only_unsubmitted_drafts(tmp_path):
    setup = _setup_records()
    store = EphemeralSecretStore(tmp_path / "secrets")
    prompts = ScriptedPrompts(
        [
            str(setup.target_id),
            "__create__",
            "draft-user",
            "/login",
            "/me",
            "reader",
            "new-secret",
            str(setup.admin_id),
            "submit",
        ]
    )
    wizard = CoverageSetupWizard(prompts=prompts, secret_store=store)
    request = wizard.run(setup.url)
    wizard.cleanup_unsubmitted_secrets()
    with session_scope() as session:
        assert session.get(CredentialProfile, request.user_profile_id) is None
        assert session.get(CredentialProfile, setup.user_id) is not None
        assert session.get(CredentialProfile, setup.admin_id) is not None
        assert session.get(Target, setup.target_id) is not None
    assert list(store.root.glob("*.secret")) == []


def test_interrupt_during_confirmed_secret_replacement_preserves_existing_refs(tmp_path):
    setup = _setup_records()
    store = EphemeralSecretStore(tmp_path / "secrets")
    expired = store.write("old")
    store.delete(expired)
    with session_scope() as session:
        for profile_id in (setup.user_id, setup.admin_id):
            session.get(CredentialProfile, profile_id).secret_ref = expired

    class Prompts(ScriptedPrompts):
        def secret(self, label):
            if self.hidden_values:
                raise KeyboardInterrupt
            return super().secret(label)

    class Sessions:
        def ensure_valid_for_profile(self, session, profile_id):
            raise InputValidationError("expired")

    prompts = Prompts(
        [str(setup.target_id), str(setup.user_id), str(setup.admin_id), "submit", "replacement"]
    )
    with pytest.raises(KeyboardInterrupt):
        CoverageSetupWizard(
            prompts=prompts, secret_store=store, auth_session_service=Sessions()
        ).run(setup.url)
    assert list(store.root.glob("*.secret")) == []
    with session_scope() as session:
        assert session.get(CredentialProfile, setup.user_id).secret_ref == expired
        assert session.get(CredentialProfile, setup.admin_id).secret_ref == expired


def test_wizard_rejects_configuration_changed_during_session_preparation(tmp_path):
    setup = _setup_records()
    store = EphemeralSecretStore(tmp_path / "secrets")
    expired = store.write("old")
    store.delete(expired)
    with session_scope() as session:
        session.get(CredentialProfile, setup.user_id).secret_ref = expired

    class Sessions:
        def ensure_valid_for_profile(self, session, profile_id):
            session.get(CredentialProfile, profile_id).name = "changed-config"
            return object()

    prompts = ScriptedPrompts(
        [str(setup.target_id), str(setup.user_id), str(setup.admin_id), "submit"]
    )
    with pytest.raises(InputValidationError, match="配置已变化"):
        CoverageSetupWizard(
            prompts=prompts, secret_store=store, auth_session_service=Sessions()
        ).run(setup.url)


def _create_source_run(url, target_id):
    from app.models.scan_run import ScanRun

    with session_scope() as session:
        session.add(
            ScanRun(
                id=17, target_id=target_id, input_url=url, normalized_url=url, status="completed"
            )
        )


def test_wizard_reloads_configuration_changed_while_confirmation_is_open():
    setup = _setup_records()

    class Prompts(ScriptedPrompts):
        def choose(self, label, choices):
            answer = super().choose(label, choices)
            if answer == "submit":
                with session_scope() as external:
                    external.get(CredentialProfile, setup.admin_id).role = "auditor"
            return answer

    prompts = Prompts([str(setup.target_id), str(setup.user_id), str(setup.admin_id), "submit"])
    with pytest.raises(InputValidationError, match="配置已变化"):
        CoverageSetupWizard(prompts=prompts).run(setup.url)


def test_cleanup_preserves_draft_and_secret_adopted_by_another_run(tmp_path):
    from app.models.scan_context import ScanContext
    from app.models.scan_run import ScanRun

    setup = _setup_records()
    store = EphemeralSecretStore(tmp_path / "secrets")
    prompts = ScriptedPrompts(
        [
            str(setup.target_id),
            "__create__",
            "draft-user",
            "/login",
            "/me",
            "reader",
            "new-secret",
            str(setup.admin_id),
            "submit",
        ]
    )
    wizard = CoverageSetupWizard(prompts=prompts, secret_store=store)
    request = wizard.run(setup.url)
    with session_scope() as session:
        profile = session.get(CredentialProfile, request.user_profile_id)
        reference = profile.secret_ref
        run = ScanRun(input_url=setup.url, normalized_url=setup.url, target_id=setup.target_id)
        session.add(run)
        session.flush()
        session.add(
            ScanContext(
                scan_run_id=run.id,
                kind="user",
                credential_profile_id=profile.id,
                temporary_secret_ref=reference,
            )
        )
    wizard.cleanup_unsubmitted_secrets()
    with session_scope() as session:
        assert session.get(CredentialProfile, request.user_profile_id) is not None
    assert store.read(reference) == "new-secret"


@pytest.mark.parametrize("change", ["edited", "cleanup_failure"])
def test_cleanup_preserves_changed_draft_or_failed_compensation(tmp_path, change):
    from contextlib import contextmanager

    from sqlalchemy.exc import SQLAlchemyError

    setup = _setup_records()
    store = EphemeralSecretStore(tmp_path / "secrets")
    prompts = ScriptedPrompts(
        [
            str(setup.target_id),
            "__create__",
            "draft-user",
            "/login",
            "/me",
            "reader",
            "new-secret",
            str(setup.admin_id),
            "submit",
        ]
    )
    wizard = CoverageSetupWizard(prompts=prompts, secret_store=store)
    request = wizard.run(setup.url)
    with session_scope() as session:
        profile = session.get(CredentialProfile, request.user_profile_id)
        reference = profile.secret_ref
        if change == "edited":
            profile.name = "externally-edited"

    @contextmanager
    def failing_commit():
        with session_scope() as session:
            yield session
            raise SQLAlchemyError("private-database-detail")

    if change == "cleanup_failure":
        wizard.session_factory = failing_commit
    wizard.cleanup_unsubmitted_secrets()
    assert "private-database-detail" not in prompts.rendered_text
    with session_scope() as session:
        assert session.get(CredentialProfile, request.user_profile_id) is not None
    assert store.read(reference) == "new-secret"
