from app.db.bootstrap import session_scope
from app.integrations.auth.http_adapter import HttpLoginAdapter
from app.models.enums import AuthType, SessionStatus, TargetStatus, TargetType
from app.schemas.credential import CredentialProfileCreate
from app.schemas.target import TargetCreate
from app.services.credentials import CredentialProfileService
from app.services.sessions import AuthSessionService
from app.services.targets import TargetService


def test_auth_session_lifecycle(auth_http_transport, tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "http-login.yaml"
    config_path.write_text(
        """
adapter: http
request_timeout_seconds: 5
retry_attempts: 1
session_type: http_cookie_jar
login_request:
  method: POST
  url: /login
  body_type: json
  body:
    username: "{{ username }}"
    password: "{{ password }}"
  expected_status: 200
  success_contains: '"authenticated": true'
validate_request:
  method: GET
  url: /me
  expected_status: 200
  success_contains: '"authenticated": true'
refresh_request:
  method: POST
  url: /refresh
  expected_status: 200
  success_contains: '"refreshed": true'
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("GARDEN_TEST_HTTP_SECRET", "demo-admin-password")

    with session_scope() as session:
        target = TargetService().create(
            session,
            TargetCreate(
                name="lifecycle-target",
                base_url="http://localhost:9402",
                type=TargetType.WEB,
                owner="appsec",
                tags=["demo"],
                status=TargetStatus.ACTIVE,
            ),
        )
        CredentialProfileService().create(
            session,
            CredentialProfileCreate(
                target_id=target.id,
                name="lifecycle-profile",
                role="admin",
                auth_type=AuthType.PASSWORD,
                username="admin@example.local",
                secret_ref="env://GARDEN_TEST_HTTP_SECRET",
                login_config_path=str(config_path),
            ),
        )

    service = AuthSessionService(http_adapter=HttpLoginAdapter(transport=auth_http_transport))
    with session_scope() as session:
        login_result = service.login_test(session, "lifecycle-target", "lifecycle-profile")
        assert login_result.success is True
        stored_sessions = service.list(session)
        assert len(stored_sessions) == 1
        auth_session = stored_sessions[0]
        assert auth_session.status == SessionStatus.ACTIVE.value

    with session_scope() as session:
        validation_result = service.validate(session, 1)
        assert validation_result.valid is True
        refreshed = service.refresh(session, 1)
        assert refreshed.success is True
        assert refreshed.status == SessionStatus.REFRESHED
