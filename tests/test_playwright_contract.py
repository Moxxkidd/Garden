from app.db.bootstrap import session_scope
from app.integrations.auth.playwright_adapter import PlaywrightLoginAdapter
from app.models.enums import AuthType, LoginFailureReason, TargetStatus, TargetType
from app.schemas.auth import PlaywrightLoginConfig
from app.schemas.credential import CredentialProfileCreate
from app.schemas.target import TargetCreate
from app.services.credentials import CredentialProfileService
from app.services.targets import TargetService


class FakePlaywrightGateway:
    def login(self, config, target, username, password):
        return {
            "storage_state": {
                "cookies": [{"name": "demo"}],
                "origins": [{"origin": target.base_url}],
            },
            "current_url": f"{target.base_url}/demo/auth/ui/home",
            "page_text": "Demo Home",
        }

    def validate(self, config, target, storage_state):
        return {"current_url": f"{target.base_url}/demo/auth/ui/home", "page_text": "Demo Home"}


class FailingPlaywrightGateway:
    def login(self, config, target, username, password):
        raise TimeoutError("selector not found")

    def validate(self, config, target, storage_state):
        raise TimeoutError("selector not found")


def test_playwright_adapter_contract_success() -> None:
    adapter = PlaywrightLoginAdapter(gateway=FakePlaywrightGateway())
    config = PlaywrightLoginConfig.model_validate(
        {
            "adapter": "playwright",
            "login_url": "/demo/auth/ui/login",
            "validate_url": "/demo/auth/ui/home",
            "username_selector": "#username",
            "password_selector": "#password",
            "submit_selector": "#submit-login",
            "success_selector": "#demo-home-title",
            "success_text": "Demo Home",
            "success_url_contains": "/demo/auth/ui/home",
            "refresh_via_relogin": True,
        }
    )
    with session_scope() as session:
        target = TargetService().create(
            session,
            TargetCreate(
                name="playwright-target",
                base_url="http://localhost:9990",
                type=TargetType.WEB,
                owner="appsec",
                tags=["demo"],
                status=TargetStatus.ACTIVE,
            ),
        )
        credential = CredentialProfileService().create(
            session,
            CredentialProfileCreate(
                target_id=target.id,
                name="playwright-profile",
                role="admin",
                auth_type=AuthType.PASSWORD,
                username="admin@example.local",
                secret_ref="literal://demo-admin-password",
                login_config_path="examples/login/demo-playwright-admin.yaml",
            ),
        )
    login_result = adapter.login(target, credential, "demo-admin-password", config)
    assert login_result.success is True
    validate_result = adapter.validate(target, credential, config, login_result.storage_payload)
    assert validate_result.valid is True


def test_playwright_adapter_contract_failure() -> None:
    adapter = PlaywrightLoginAdapter(gateway=FailingPlaywrightGateway())
    config = PlaywrightLoginConfig.model_validate(
        {
            "adapter": "playwright",
            "login_url": "/demo/auth/ui/login",
            "validate_url": "/demo/auth/ui/home",
            "username_selector": "#username",
            "password_selector": "#password",
            "submit_selector": "#submit-login",
        }
    )
    with session_scope() as session:
        target = TargetService().create(
            session,
            TargetCreate(
                name="playwright-target-failure",
                base_url="http://localhost:9991",
                type=TargetType.WEB,
                owner="appsec",
                tags=["demo"],
                status=TargetStatus.ACTIVE,
            ),
        )
        credential = CredentialProfileService().create(
            session,
            CredentialProfileCreate(
                target_id=target.id,
                name="playwright-profile-failure",
                role="admin",
                auth_type=AuthType.PASSWORD,
                username="admin@example.local",
                secret_ref="literal://demo-admin-password",
                login_config_path="examples/login/demo-playwright-admin.yaml",
            ),
        )
    login_result = adapter.login(target, credential, "demo-admin-password", config)
    assert login_result.success is False
    assert login_result.failure_reason == LoginFailureReason.FORM_NOT_FOUND
