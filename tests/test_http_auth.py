from app.db.bootstrap import session_scope
from app.integrations.auth.http_adapter import HttpLoginAdapter
from app.models.enums import LoginFailureReason, TargetStatus, TargetType
from app.schemas.auth import HttpLoginConfig
from app.schemas.credential import CredentialProfileCreate
from app.schemas.target import TargetCreate
from app.services.credentials import CredentialProfileService
from app.services.targets import TargetService


def test_http_login_adapter_round_trip(auth_http_transport) -> None:
    adapter = HttpLoginAdapter(transport=auth_http_transport)
    config = HttpLoginConfig.model_validate(
        {
            "adapter": "http",
            "request_timeout_seconds": 5,
            "retry_attempts": 1,
            "login_request": {
                "method": "POST",
                "url": "/login",
                "body_type": "json",
                "body": {"username": "{{ username }}", "password": "{{ password }}"},
                "expected_status": 200,
                "success_contains": '"authenticated": true',
            },
            "validate_request": {
                "method": "GET",
                "url": "/me",
                "expected_status": 200,
                "success_contains": '"authenticated": true',
            },
            "refresh_request": {
                "method": "POST",
                "url": "/refresh",
                "expected_status": 200,
                "success_contains": '"refreshed": true',
            },
        }
    )

    with session_scope() as session:
        target = TargetService().create(
            session,
            TargetCreate(
                name="http-round-trip",
                base_url="http://localhost:9400",
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
                name="http-admin",
                role="admin",
                auth_type="password",
                username="admin@example.local",
                secret_ref="literal://demo-admin-password",
                login_config_path="examples/login/demo-http-admin.yaml",
            ),
        )

    login_result = adapter.login(target, credential, "demo-admin-password", config)
    assert login_result.success is True
    assert login_result.refresh_supported is True
    assert "cookies" in login_result.storage_payload

    validate_result = adapter.validate(target, credential, config, login_result.storage_payload)
    assert validate_result.valid is True

    refresh_result = adapter.refresh(
        target,
        credential,
        "demo-admin-password",
        config,
        login_result.storage_payload,
    )
    assert refresh_result.success is True


def test_http_login_adapter_rejected_login(auth_http_transport) -> None:
    adapter = HttpLoginAdapter(transport=auth_http_transport)
    config = HttpLoginConfig.model_validate(
        {
            "adapter": "http",
            "login_request": {
                "method": "POST",
                "url": "/login",
                "body_type": "json",
                "body": {"username": "{{ username }}", "password": "{{ password }}"},
                "expected_status": 200,
            },
            "validate_request": {"method": "GET", "url": "/me", "expected_status": 200},
        }
    )
    with session_scope() as session:
        target = TargetService().create(
            session,
            TargetCreate(
                name="http-failure",
                base_url="http://localhost:9401",
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
                name="http-admin-failure",
                role="admin",
                auth_type="password",
                username="admin@example.local",
                secret_ref="literal://wrong-password",
                login_config_path="examples/login/demo-http-admin.yaml",
            ),
        )

    login_result = adapter.login(target, credential, "wrong-password", config)
    assert login_result.success is False
    assert login_result.failure_reason == LoginFailureReason.LOGIN_REJECTED
