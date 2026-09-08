from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from app.core.settings import Settings
from app.db.bootstrap import session_scope
from app.models.enums import AssessmentMode, AuthType, TargetStatus, TargetType
from app.schemas.assessment import AssessmentStartRequest
from app.schemas.credential import CredentialProfileCreate
from app.schemas.scan import ScanOptions
from app.schemas.target import TargetCreate
from app.services.context_collection import (
    ContextCollectionService,
    DefaultContextCollectionGateway,
)
from app.services.context_establishment import ContextEstablishmentService
from app.services.credentials import CredentialProfileService
from app.services.inventory_collection import InventoryCollectionService
from app.services.scan_application import InlineScanDispatcher, ScanApplicationService
from app.services.scan_network import HttpScanGateway, TargetNetworkPolicy
from app.services.scan_pipeline import ScanPipeline
from app.services.scan_reporting import ScanReportService
from app.services.session_storage import SessionStorageService
from app.services.sessions import AuthSessionService
from app.services.targets import TargetService
from tests.fixtures.auth_coverage_site.app import app as fixture_app


def _reserve_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture
def auth_coverage_site(monkeypatch):
    monkeypatch.setenv("GARDEN_E2E_USER_PASSWORD", "local-user-secret")
    monkeypatch.setenv("GARDEN_E2E_ADMIN_PASSWORD", "local-admin-secret")
    port = _reserve_port()
    server = uvicorn.Server(
        uvicorn.Config(
            fixture_app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        pytest.fail("localhost coverage fixture did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _login_config(role: str) -> str:
    return str(
        (
            Path(__file__).parent / "fixtures" / "auth_coverage_site" / f"login-config-{role}.yaml"
        ).resolve()
    )


def _application(tmp_path: Path) -> ScanApplicationService:
    settings = Settings(
        GARDEN_ALLOW_NON_LOCAL_TARGETS=False,
        GARDEN_ALLOW_PRIVATE_TARGETS=False,
        GARDEN_SCAN_OVERALL_TIMEOUT_SECONDS=60,
    )
    policy = TargetNetworkPolicy(settings)
    http_gateway = HttpScanGateway(policy)
    session_storage = SessionStorageService(tmp_path / "session-payloads")
    auth_service = AuthSessionService(storage_service=session_storage)
    context_service = ContextEstablishmentService(auth_service=auth_service)
    inventory_service = InventoryCollectionService(storage_service=session_storage)
    collection_service = ContextCollectionService(
        gateway=DefaultContextCollectionGateway(
            http_gateway=http_gateway,
            inventory_service=inventory_service,
        ),
        storage_service=session_storage,
    )
    pipeline = ScanPipeline(
        policy=policy,
        gateway=http_gateway,
        context_service=context_service,
        context_collection_service=collection_service,
        report_service=ScanReportService(tmp_path / "reports"),
    )
    return ScanApplicationService(
        settings=settings,
        pipeline=pipeline,
        dispatcher=InlineScanDispatcher(),
    )


def test_real_browser_three_context_coverage_is_passive_and_role_aware(
    auth_coverage_site: str,
    tmp_path: Path,
) -> None:
    with session_scope() as session:
        target = TargetService().create(
            session,
            TargetCreate(
                name="localhost-auth-coverage",
                base_url=auth_coverage_site,
                type=TargetType.WEB,
                owner="pytest",
                tags=["localhost", "e2e"],
                status=TargetStatus.ACTIVE,
            ),
        )
        profiles = CredentialProfileService()
        user = profiles.create(
            session,
            CredentialProfileCreate(
                target_id=target.id,
                name="coverage-user",
                role="user",
                auth_type=AuthType.PASSWORD,
                username="coverage-user",
                secret_ref="env://GARDEN_E2E_USER_PASSWORD",
                login_config_path=_login_config("user"),
            ),
        )
        admin = profiles.create(
            session,
            CredentialProfileCreate(
                target_id=target.id,
                name="coverage-admin",
                role="admin",
                auth_type=AuthType.PASSWORD,
                username="coverage-admin",
                secret_ref="env://GARDEN_E2E_ADMIN_PASSWORD",
                login_config_path=_login_config("admin"),
            ),
        )
        target_id, user_id, admin_id = target.id, user.id, admin.id

    service = _application(tmp_path)
    result = service.start_assessment(
        AssessmentStartRequest(
            url=auth_coverage_site,
            mode=AssessmentMode.AUTHENTICATED_COVERAGE,
            target_id=target_id,
            user_profile_id=user_id,
            admin_profile_id=admin_id,
            options=ScanOptions(max_pages=10, max_depth=2, overall_timeout_seconds=60),
        )
    )

    assert result.status == "completed", (
        result.error_code,
        result.error_message,
        [
            (context.kind, context.status, context.error_code, context.error_message)
            for context in result.contexts
        ],
    )
    assert result.completeness == "complete"
    assert result.context_counts == {"anonymous": 1, "user": 1, "admin": 1}
    assert result.replay_count == 0
    differences = {item.identity_key: item for item in service.list_coverage_differences(result.id)}
    shared = differences["GET:/shared"]
    stable_fields = ("status_code", "url", "title", "content_type", "content_signature")
    shared_field_matches = {
        field: len({summary.get(field) for summary in shared.context_summaries.values()}) == 1
        for field in stable_fields
    }
    assert shared.classification == "shared", shared_field_matches
    assert differences["GET:/account"].classification == "user_only"
    assert differences["GET:/admin/users"].classification == "admin_only"
    assert differences["GET:/anonymous-banner"].classification == "unexpectedly_anonymous"
    assert differences["GET:/api/profile"].classification == "inconsistent"
    report = service.read_report(result.id)
    assert "主动权限重放未执行" in report
    assert "覆盖差异不是已确认漏洞" in report
    assert "local-user-secret" not in report
    assert "local-admin-secret" not in report
