from pathlib import Path

from app.db.bootstrap import session_scope
from app.integrations.auth.http_adapter import HttpLoginAdapter
from app.schemas.credential import CredentialProfileCreate
from app.schemas.inventory import InventoryBuildControls
from app.schemas.target import TargetCreate
from app.services.credentials import CredentialProfileService
from app.services.inventory import InventoryBuildService
from app.services.sessions import AuthSessionService
from app.services.targets import TargetService


def _write_http_login_config(path: Path) -> None:
    path.write_text(
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


def test_inventory_build_from_target_profile(
    auth_http_transport,
    fake_inventory_collection_service,
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "inventory-http-login.yaml"
    _write_http_login_config(config_path)
    monkeypatch.setenv("GARDEN_INVENTORY_SECRET", "demo-admin-password")

    with session_scope() as session:
        target = TargetService().create(
            session,
            TargetCreate(
                name="inventory-target",
                base_url="http://localhost:9800",
                type="web",
                owner="appsec",
                tags=["demo"],
                status="active",
            ),
        )
        CredentialProfileService().create(
            session,
            CredentialProfileCreate(
                target_id=target.id,
                name="inventory-profile",
                role="admin",
                auth_type="password",
                username="admin@example.local",
                secret_ref="env://GARDEN_INVENTORY_SECRET",
                login_config_path=str(config_path),
            ),
        )

    service = InventoryBuildService(
        auth_session_service=AuthSessionService(
            http_adapter=HttpLoginAdapter(transport=auth_http_transport)
        ),
        collection_service=fake_inventory_collection_service,
    )
    with session_scope() as session:
        summary = service.build_from_target_profile(
            session,
            "inventory-target",
            "inventory-profile",
            InventoryBuildControls(),
        )
        inventory_run = service.get(session, summary.inventory_run_id)

    assert summary.counts.pages == 4
    assert summary.counts.endpoints == 8
    assert summary.counts.parameters == 16
    assert summary.counts.annotations == 26
    assert inventory_run.started_from_url.endswith("/demo/auth/ui/home")
    assert inventory_run.scan_job.status == "completed"
    assert inventory_run.auth_session.status == "active"


def test_inventory_build_reuses_existing_session(
    auth_http_transport,
    fake_inventory_collection_service,
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "inventory-http-login.yaml"
    _write_http_login_config(config_path)
    monkeypatch.setenv("GARDEN_REUSE_SECRET", "demo-admin-password")

    auth_service = AuthSessionService(http_adapter=HttpLoginAdapter(transport=auth_http_transport))
    with session_scope() as session:
        target = TargetService().create(
            session,
            TargetCreate(
                name="reuse-target",
                base_url="http://localhost:9801",
                type="web",
                owner="appsec",
                tags=["demo"],
                status="active",
            ),
        )
        CredentialProfileService().create(
            session,
            CredentialProfileCreate(
                target_id=target.id,
                name="reuse-profile",
                role="admin",
                auth_type="password",
                username="admin@example.local",
                secret_ref="env://GARDEN_REUSE_SECRET",
                login_config_path=str(config_path),
            ),
        )
        login_result = auth_service.login_test(session, "reuse-target", "reuse-profile")
        existing_session_id = login_result.created_session_id

    service = InventoryBuildService(
        auth_session_service=auth_service,
        collection_service=fake_inventory_collection_service,
    )
    with session_scope() as session:
        summary = service.build_from_session(
            session,
            existing_session_id or 0,
            InventoryBuildControls(max_pages=10, max_depth=1, max_requests=20),
        )

    assert summary.auth_session_id == existing_session_id
    assert summary.counts.pages == 4
