import json
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie

import httpx
import pytest

from app.core.settings import clear_settings_cache, get_settings
from app.db.bootstrap import init_database, reset_database_state, session_scope
from app.integrations.evidence.playwright_capture import PageCaptureArtifact
from app.integrations.inventory.playwright_gateway import (
    InventoryCollectionResult,
    ObservedEndpoint,
    ObservedPage,
)
from app.main import create_app
from app.models.auth_session import AuthSession
from app.models.enums import (
    AuthType,
    InventoryRunStatus,
    JobStatus,
    SessionStatus,
    SessionType,
    TargetStatus,
    TargetType,
)
from app.models.inventory_annotation import InventoryAnnotation
from app.models.inventory_endpoint import InventoryEndpoint
from app.models.inventory_page import InventoryPage
from app.models.inventory_parameter import InventoryParameter
from app.models.inventory_run import InventoryRun
from app.schemas.credential import CredentialProfileCreate
from app.schemas.job import ScanJobCreate
from app.schemas.target import TargetCreate
from app.services.credentials import CredentialProfileService
from app.services.evidence import EvidenceService
from app.services.evidence_storage import EvidenceStorageService
from app.services.findings import CheckRunService
from app.services.inventory_collection import InventoryCollectionService
from app.services.jobs import ScanJobService
from app.services.session_storage import SessionStorageService
from app.services.targets import TargetService


class _MockAuthTransport:
    users = {
        "admin@example.local": {"password": "demo-admin-password", "role": "admin"},
        "user@example.local": {"password": "demo-user-password", "role": "user"},
    }

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, object]] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        current_url = (
            f"{request.url.scheme}://{request.url.host}:{request.url.port}/demo/auth/ui/home"
        )
        if request.url.path == "/login" and request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            username = str(body.get("username", ""))
            password = str(body.get("password", ""))
            user = self.users.get(username)
            if user is None or user["password"] != password:
                return httpx.Response(
                    401,
                    json={"authenticated": False, "detail": "login rejected"},
                )
            token = f"token-{username}"
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            self.sessions[token] = {
                "username": username,
                "role": user["role"],
                "expires_at": expires_at,
            }
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "set-cookie": f"demo_auth={token}; HttpOnly; Path=/",
                },
                text=json.dumps(
                    {
                        "authenticated": True,
                        "role": user["role"],
                        "expires_at": expires_at.isoformat(),
                        "current_url": current_url,
                    }
                ),
            )
        if request.url.path == "/me" and request.method == "GET":
            token = self._get_cookie(request, "demo_auth")
            session_record = self.sessions.get(token)
            if session_record is None or session_record["expires_at"] < datetime.now(timezone.utc):
                return httpx.Response(
                    401,
                    headers={"content-type": "application/json"},
                    text=json.dumps({"authenticated": False, "detail": "session expired"}),
                )
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                text=json.dumps(
                    {
                        "authenticated": True,
                        "username": session_record["username"],
                        "role": session_record["role"],
                        "expires_at": session_record["expires_at"].isoformat(),
                        "current_url": current_url,
                    }
                ),
            )
        if request.url.path == "/refresh" and request.method == "POST":
            token = self._get_cookie(request, "demo_auth")
            session_record = self.sessions.get(token)
            if session_record is None:
                return httpx.Response(
                    401,
                    headers={"content-type": "application/json"},
                    text=json.dumps({"refreshed": False, "detail": "session expired"}),
                )
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
            session_record["expires_at"] = expires_at
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "set-cookie": f"demo_auth={token}; HttpOnly; Path=/",
                },
                text=json.dumps(
                    {
                        "refreshed": True,
                        "expires_at": expires_at.isoformat(),
                        "current_url": current_url,
                    }
                ),
            )
        return httpx.Response(
            404,
            headers={"content-type": "application/json"},
            text=json.dumps({"detail": "not found"}),
        )

    def _get_cookie(self, request: httpx.Request, name: str) -> str | None:
        raw_cookie = request.headers.get("cookie")
        if not raw_cookie:
            return None
        cookie = SimpleCookie()
        cookie.load(raw_cookie)
        morsel = cookie.get(name)
        return morsel.value if morsel else None


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'garden-test.db'}"
    monkeypatch.setenv("GARDEN_DATABASE_URL", database_url)
    monkeypatch.setenv("GARDEN_ALLOW_NON_LOCAL_TARGETS", "false")
    monkeypatch.setenv("GARDEN_DEMO_ADMIN_PASSWORD", "demo-admin-password")
    monkeypatch.setenv("GARDEN_DEMO_USER_PASSWORD", "demo-user-password")
    clear_settings_cache()
    reset_database_state()
    init_database(get_settings().database_url)
    yield
    reset_database_state()
    clear_settings_cache()


@pytest.fixture
def auth_http_transport():
    transport = _MockAuthTransport()
    return httpx.MockTransport(transport.handle)


@pytest.fixture
def app():
    return create_app()


class FakeInventoryGateway:
    def collect(self, target, start_url, controls, session_type, session_payload):
        origin = f"{httpx.URL(target.base_url).scheme}://{httpx.URL(target.base_url).netloc}"
        pages = [
            ObservedPage(
                url=f"{origin}/demo/auth/ui/home",
                title="Demo Home",
                visited_at=datetime.now(timezone.utc),
                depth=0,
                discovered_urls=[
                    f"{origin}/demo/auth/app/admin/users",
                    f"{origin}/demo/auth/app/manage/config",
                    f"{origin}/demo/auth/app/export-center",
                ],
                status_code=200,
                cache_control="private, no-store",
                text_preview="Authenticated demo home.",
            ),
            ObservedPage(
                url=f"{origin}/demo/auth/app/admin/users",
                title="Manage Users",
                visited_at=datetime.now(timezone.utc),
                depth=1,
                discovered_urls=[],
                status_code=200,
                cache_control="public, max-age=600",
                text_preview=(
                    "DEBUG banner active for staging-admin.internal.local "
                    "Support: support@app.internal.local"
                ),
            ),
            ObservedPage(
                url=f"{origin}/demo/auth/app/manage/config",
                title="Config Console",
                visited_at=datetime.now(timezone.utc),
                depth=1,
                discovered_urls=[],
                status_code=200,
                cache_control="public, max-age=300",
                text_preview=(
                    "Config path /srv/garden/config/settings.py Environment non-production staging"
                ),
            ),
            ObservedPage(
                url=f"{origin}/demo/auth/app/export-center",
                title="Export and Upload Hub",
                visited_at=datetime.now(timezone.utc),
                depth=1,
                discovered_urls=[],
                status_code=200,
                cache_control="public, max-age=900",
                text_preview="Build version 2026.04.1 trace_id=demo-trace-001",
            ),
            ObservedPage(
                url=f"{origin}/demo/auth/app/admin/users",
                title="Manage Users",
                visited_at=datetime.now(timezone.utc),
                depth=1,
                discovered_urls=[],
                status_code=200,
                cache_control="public, max-age=600",
                text_preview=(
                    "DEBUG banner active for staging-admin.internal.local "
                    "Support: support@app.internal.local"
                ),
            ),
        ]
        endpoints = [
            ObservedEndpoint(
                method="GET",
                url=f"{origin}/demo/auth/app/api/bootstrap",
                path="/demo/auth/app/api/bootstrap",
                status_code=200,
                observed_at=datetime.now(timezone.utc),
                cache_control="private, no-store",
                content_type="application/json",
                response_preview='{"ok":true,"session":"current","role":"admin"}',
                parameters={"query": ["session", "role"]},
            ),
            ObservedEndpoint(
                method="GET",
                url=f"{origin}/demo/auth/app/api/admin/users",
                path="/demo/auth/app/api/admin/users",
                status_code=200,
                observed_at=datetime.now(timezone.utc),
                cache_control="private, no-store",
                content_type="application/json",
                response_preview=(
                    '{"ok":true,"traceId":"trace-admin-001","support":"support@app.internal.local"}'
                ),
                parameters={"query": ["userId", "role"]},
            ),
            ObservedEndpoint(
                method="GET",
                url=f"{origin}/demo/auth/app/api/admin/users",
                path="/demo/auth/app/api/admin/users",
                status_code=200,
                observed_at=datetime.now(timezone.utc),
                cache_control="private, no-store",
                content_type="application/json",
                response_preview=(
                    '{"ok":true,"traceId":"trace-admin-001","support":"support@app.internal.local"}'
                ),
                parameters={"query": ["userId", "role"]},
            ),
            ObservedEndpoint(
                method="POST",
                url=f"{origin}/demo/auth/app/api/admin/export",
                path="/demo/auth/app/api/admin/export",
                status_code=200,
                observed_at=datetime.now(timezone.utc),
                cache_control="private, no-store",
                content_type="application/json",
                response_preview='{"ok":true,"version":"2026.04.1"}',
                parameters={"body": ["userId", "role", "token"]},
            ),
            ObservedEndpoint(
                method="GET",
                url=f"{origin}/demo/auth/app/api/manage/config",
                path="/demo/auth/app/api/manage/config",
                status_code=200,
                observed_at=datetime.now(timezone.utc),
                cache_control="public, max-age=600",
                content_type="application/json",
                response_preview=json.dumps(
                    {
                        "banner": "DEBUG toolbar enabled",
                        "traceback": (
                            "Traceback (most recent call last): File '/srv/garden/app.py', line 42"
                        ),
                        "internalHost": "config-node.internal.local",
                    }
                ),
                set_cookie_names=["debug_pref"],
                cookie_issue_flags=["missing_httponly", "missing_secure", "missing_samesite"],
                parameters={"query": ["debug", "actuator"]},
            ),
            ObservedEndpoint(
                method="POST",
                url=f"{origin}/demo/auth/app/api/manage/import",
                path="/demo/auth/app/api/manage/import",
                status_code=200,
                observed_at=datetime.now(timezone.utc),
                cache_control="private, no-store",
                content_type="application/json",
                response_preview='{"ok":true,"environment":"staging"}',
                parameters={"form": ["key", "secret"]},
            ),
            ObservedEndpoint(
                method="GET",
                url=f"{origin}/demo/auth/app/api/export/download",
                path="/demo/auth/app/api/export/download",
                status_code=200,
                observed_at=datetime.now(timezone.utc),
                cache_control="public, max-age=600",
                content_type="application/json",
                response_preview='{"ok":true,"traceId":"trace-export-101"}',
                parameters={"query": ["format", "session"]},
            ),
            ObservedEndpoint(
                method="POST",
                url=f"{origin}/demo/auth/app/api/upload",
                path="/demo/auth/app/api/upload",
                status_code=200,
                observed_at=datetime.now(timezone.utc),
                cache_control="private, no-store",
                content_type="application/json",
                response_preview='{"ok":true,"environment":"sandbox"}',
                parameters={"form": ["password", "token"]},
            ),
            ObservedEndpoint(
                method="GET",
                url=f"{origin}/demo/auth/app/api/actuator/health",
                path="/demo/auth/app/api/actuator/health",
                status_code=200,
                observed_at=datetime.now(timezone.utc),
                cache_control="private, no-store",
                content_type="application/json",
                response_preview='{"status":"UP","version":"1.4.2","internalHost":"health-node.internal.local"}',
                parameters={"query": ["key"]},
            ),
        ]
        return InventoryCollectionResult(start_url=start_url, pages=pages, endpoints=endpoints)


class FakeEvidenceGateway:
    def capture_page(self, *, base_url, url, session_type, session_payload, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
            b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\x1dc```\x00"
            b"\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return PageCaptureArtifact(
            page_title="Captured Demo Page",
            final_url=url,
            status_code=200,
            screenshot_path=str(output_path),
        )


@pytest.fixture
def fake_inventory_collection_service(tmp_path):
    storage_service = SessionStorageService(base_directory=tmp_path / "session_payloads")
    return InventoryCollectionService(
        storage_service=storage_service,
        gateway=FakeInventoryGateway(),
    )


@pytest.fixture
def fake_evidence_service(tmp_path):
    return EvidenceService(
        storage_service=EvidenceStorageService(base_directory=tmp_path / "evidence"),
        session_storage_service=SessionStorageService(base_directory=tmp_path / "session_payloads"),
        screenshot_gateway=FakeEvidenceGateway(),
    )


@pytest.fixture
def seeded_records(tmp_path):
    target_service = TargetService()
    credential_service = CredentialProfileService()
    job_service = ScanJobService()
    storage_service = SessionStorageService(base_directory=tmp_path / "session_payloads")
    config_path = tmp_path / "seeded-login.yaml"
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
    with session_scope() as session:
        target = target_service.create(
            session,
            TargetCreate(
                name="local-admin",
                base_url="http://localhost:8080",
                type=TargetType.ADMIN,
                owner="appsec",
                tags=["demo", "local"],
                status=TargetStatus.ACTIVE,
            ),
        )
        credential = credential_service.create(
            session,
            CredentialProfileCreate(
                target_id=target.id,
                name="admin-primary",
                role="administrator",
                auth_type=AuthType.PASSWORD,
                username="admin@example.local",
                secret_ref="env://GARDEN_DEMO_ADMIN_PASSWORD",
                login_config_path=str(config_path),
            ),
        )
        job = job_service.create(
            session,
            ScanJobCreate(
                target_id=target.id,
                credential_profile_id=credential.id,
                status=JobStatus.COMPLETED,
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
                summary="Initial management smoke job.",
                error_message=None,
            ),
        )
        auth_session = AuthSession(
            target_id=target.id,
            credential_profile_id=credential.id,
            scan_job_id=job.id,
            status=SessionStatus.ACTIVE.value,
            session_type=SessionType.HTTP_COOKIE_JAR.value,
            refresh_supported=True,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            session_metadata_redacted={"adapter": "http", "cookie_names": ["demo_auth"]},
            storage_ref="",
            last_error=None,
        )
        session.add(auth_session)
        session.flush()
        auth_session.storage_ref = storage_service.write_payload(
            auth_session.id,
            {"cookies": {"demo_auth": "seeded-token"}},
        )
        session.flush()
    return {"target": target, "credential": credential, "job": job, "auth_session": auth_session}


@pytest.fixture
def seeded_inventory(seeded_records):
    with session_scope() as session:
        inventory_run = InventoryRun(
            target_id=seeded_records["target"].id,
            credential_profile_id=seeded_records["credential"].id,
            auth_session_id=seeded_records["auth_session"].id,
            scan_job_id=seeded_records["job"].id,
            status=InventoryRunStatus.COMPLETED.value,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            started_from_url="http://localhost:8080/demo/auth/ui/home",
            max_pages=25,
            max_depth=2,
            max_requests=100,
            delay_ms=250,
            include_rules=[],
            exclude_rules=[],
            pages_count=1,
            endpoints_count=1,
            parameters_count=1,
            annotations_count=1,
            summary="Seeded inventory run.",
        )
        session.add(inventory_run)
        session.flush()
        session.add(
            InventoryPage(
                inventory_run_id=inventory_run.id,
                url="http://localhost:8080/demo/auth/ui/home",
                title="Demo Home",
                status_code=200,
                cache_control="private, no-store",
                text_preview="Authenticated demo home.",
                depth=0,
                first_visited_at=datetime.now(timezone.utc),
                last_visited_at=datetime.now(timezone.utc),
                visit_count=1,
            )
        )
        session.add(
            InventoryPage(
                inventory_run_id=inventory_run.id,
                url="http://localhost:8080/demo/auth/app/manage/config",
                title="Config Console",
                status_code=200,
                cache_control="public, max-age=300",
                text_preview=(
                    "DEBUG banner active. Config path /srv/garden/config/settings.py "
                    "support@app.internal.local staging config-node.internal.local"
                ),
                depth=1,
                first_visited_at=datetime.now(timezone.utc),
                last_visited_at=datetime.now(timezone.utc),
                visit_count=1,
            )
        )
        endpoint = InventoryEndpoint(
            inventory_run_id=inventory_run.id,
            method="GET",
            url="http://localhost:8080/demo/auth/app/api/admin/users",
            path="/demo/auth/app/api/admin/users",
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
            request_count=1,
            cache_control="private, no-store",
            content_type="application/json",
            response_preview='{"traceId":"trace-admin-001","support":"support@app.internal.local"}',
            set_cookie_names=[],
            cookie_issue_flags=[],
            status_codes_observed=[200],
        )
        session.add(endpoint)
        session.flush()
        session.add(
            InventoryParameter(
                inventory_run_id=inventory_run.id,
                inventory_endpoint_id=endpoint.id,
                source_type="query",
                name="userId",
                first_seen_at=datetime.now(timezone.utc),
                last_seen_at=datetime.now(timezone.utc),
                seen_count=1,
                sensitive=True,
            )
        )
        session.add(
            InventoryAnnotation(
                inventory_run_id=inventory_run.id,
                subject_type="endpoint",
                subject_ref="endpoint:GET:/demo/auth/app/api/admin/users",
                marker="admin",
                reason="path contains 'admin'",
            )
        )
        manage_endpoint = InventoryEndpoint(
            inventory_run_id=inventory_run.id,
            method="GET",
            url="http://localhost:8080/demo/auth/app/api/manage/config",
            path="/demo/auth/app/api/manage/config",
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
            request_count=1,
            cache_control="public, max-age=600",
            content_type="application/json",
            response_preview=json.dumps(
                {
                    "banner": "DEBUG toolbar enabled",
                    "traceback": (
                        "Traceback (most recent call last): File '/srv/garden/app.py', line 42"
                    ),
                    "internalHost": "config-node.internal.local",
                }
            ),
            set_cookie_names=["debug_pref"],
            cookie_issue_flags=["missing_httponly", "missing_secure", "missing_samesite"],
            status_codes_observed=[200],
        )
        session.add(manage_endpoint)
        session.flush()
        session.add_all(
            [
                InventoryParameter(
                    inventory_run_id=inventory_run.id,
                    inventory_endpoint_id=manage_endpoint.id,
                    source_type="query",
                    name="debug",
                    first_seen_at=datetime.now(timezone.utc),
                    last_seen_at=datetime.now(timezone.utc),
                    seen_count=1,
                    sensitive=False,
                ),
                InventoryParameter(
                    inventory_run_id=inventory_run.id,
                    inventory_endpoint_id=manage_endpoint.id,
                    source_type="query",
                    name="actuator",
                    first_seen_at=datetime.now(timezone.utc),
                    last_seen_at=datetime.now(timezone.utc),
                    seen_count=1,
                    sensitive=False,
                ),
                InventoryAnnotation(
                    inventory_run_id=inventory_run.id,
                    subject_type="page",
                    subject_ref="page:/demo/auth/app/manage/config",
                    marker="manage",
                    reason="path contains 'manage'",
                ),
                InventoryAnnotation(
                    inventory_run_id=inventory_run.id,
                    subject_type="page",
                    subject_ref="page:/demo/auth/app/manage/config",
                    marker="config",
                    reason="path contains 'config'",
                ),
                InventoryAnnotation(
                    inventory_run_id=inventory_run.id,
                    subject_type="endpoint",
                    subject_ref="endpoint:GET:/demo/auth/app/api/manage/config",
                    marker="manage",
                    reason="path contains 'manage'",
                ),
                InventoryAnnotation(
                    inventory_run_id=inventory_run.id,
                    subject_type="endpoint",
                    subject_ref="endpoint:GET:/demo/auth/app/api/manage/config",
                    marker="config",
                    reason="path contains 'config'",
                ),
                InventoryAnnotation(
                    inventory_run_id=inventory_run.id,
                    subject_type="endpoint",
                    subject_ref="endpoint:GET:/demo/auth/app/api/actuator/health",
                    marker="actuator",
                    reason="path contains 'actuator'",
                ),
                InventoryAnnotation(
                    inventory_run_id=inventory_run.id,
                    subject_type="endpoint",
                    subject_ref="endpoint:POST:/demo/auth/app/api/manage/import",
                    marker="import",
                    reason="path contains 'import'",
                ),
                InventoryAnnotation(
                    inventory_run_id=inventory_run.id,
                    subject_type="endpoint",
                    subject_ref="endpoint:POST:/demo/auth/app/api/upload",
                    marker="upload",
                    reason="path contains 'upload'",
                ),
            ]
        )
        import_endpoint = InventoryEndpoint(
            inventory_run_id=inventory_run.id,
            method="POST",
            url="http://localhost:8080/demo/auth/app/api/manage/import",
            path="/demo/auth/app/api/manage/import",
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
            request_count=1,
            cache_control="private, no-store",
            content_type="application/json",
            response_preview='{"environment":"staging"}',
            set_cookie_names=[],
            cookie_issue_flags=[],
            status_codes_observed=[200],
        )
        session.add(import_endpoint)
        upload_endpoint = InventoryEndpoint(
            inventory_run_id=inventory_run.id,
            method="POST",
            url="http://localhost:8080/demo/auth/app/api/upload",
            path="/demo/auth/app/api/upload",
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
            request_count=1,
            cache_control="private, no-store",
            content_type="application/json",
            response_preview='{"environment":"sandbox"}',
            set_cookie_names=[],
            cookie_issue_flags=[],
            status_codes_observed=[200],
        )
        session.add(upload_endpoint)
        actuator_endpoint = InventoryEndpoint(
            inventory_run_id=inventory_run.id,
            method="GET",
            url="http://localhost:8080/demo/auth/app/api/actuator/health",
            path="/demo/auth/app/api/actuator/health",
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
            request_count=1,
            cache_control="private, no-store",
            content_type="application/json",
            response_preview='{"status":"UP","version":"1.4.2","internalHost":"health-node.internal.local"}',
            set_cookie_names=[],
            cookie_issue_flags=[],
            status_codes_observed=[200],
        )
        session.add(actuator_endpoint)
        session.flush()
    return {**seeded_records, "inventory_run_id": inventory_run.id}


@pytest.fixture
def seeded_findings(seeded_inventory, fake_evidence_service):
    service = CheckRunService(evidence_service=fake_evidence_service)
    with session_scope() as session:
        summary = service.run_inventory_checks(session, seeded_inventory["inventory_run_id"])
        findings = service.finding_service.list(session)
    return {**seeded_inventory, "check_summary": summary, "findings": findings}
