from datetime import datetime, timezone

from app.db.bootstrap import session_scope
from app.models.auth_session import AuthSession
from app.models.credential_profile import CredentialProfile
from app.models.inventory_annotation import InventoryAnnotation
from app.models.inventory_endpoint import InventoryEndpoint
from app.models.inventory_page import InventoryPage
from app.models.inventory_parameter import InventoryParameter
from app.models.inventory_run import InventoryRun
from app.models.scan_job import ScanJob
from app.models.target import Target


def test_inventory_model_relationships_persist() -> None:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        target = Target(
            name="inventory-model-target",
            base_url="http://localhost:9700",
            type="web",
            owner="platform",
            tags=["demo"],
            status="active",
        )
        session.add(target)
        session.flush()
        credential = CredentialProfile(
            target_id=target.id,
            name="inventory-model-profile",
            role="admin",
            auth_type="password",
            username="inventory@example.local",
            secret_ref="env://INVENTORY_SECRET",
            login_config_path="examples/login/demo-http-admin.yaml",
        )
        session.add(credential)
        session.flush()
        job = ScanJob(
            target_id=target.id,
            credential_profile_id=credential.id,
            status="completed",
            started_at=now,
            finished_at=now,
            summary="Inventory model check",
            error_message=None,
        )
        session.add(job)
        session.flush()
        auth_session = AuthSession(
            target_id=target.id,
            credential_profile_id=credential.id,
            scan_job_id=job.id,
            status="active",
            session_type="http_cookie_jar",
            expires_at=now,
            last_validated_at=now,
            refresh_supported=True,
            session_metadata_redacted={"current_url": "http://localhost:9700/demo/auth/ui/home"},
            storage_ref="/tmp/session.json",
            last_error=None,
        )
        session.add(auth_session)
        session.flush()
        inventory_run = InventoryRun(
            target_id=target.id,
            credential_profile_id=credential.id,
            auth_session_id=auth_session.id,
            scan_job_id=job.id,
            status="completed",
            started_at=now,
            finished_at=now,
            started_from_url="http://localhost:9700/demo/auth/ui/home",
            max_pages=10,
            max_depth=1,
            max_requests=20,
            delay_ms=100,
            include_rules=[],
            exclude_rules=[],
            pages_count=1,
            endpoints_count=1,
            parameters_count=1,
            annotations_count=1,
            summary="Inventory model relationship check",
            error_message=None,
        )
        session.add(inventory_run)
        session.flush()
        endpoint = InventoryEndpoint(
            inventory_run_id=inventory_run.id,
            method="GET",
            url="http://localhost:9700/demo/auth/app/api/admin/users",
            path="/demo/auth/app/api/admin/users",
            first_seen_at=now,
            last_seen_at=now,
            request_count=1,
            status_codes_observed=[200],
        )
        session.add(endpoint)
        session.flush()
        session.add(
            InventoryPage(
                inventory_run_id=inventory_run.id,
                url="http://localhost:9700/demo/auth/ui/home",
                title="Demo Home",
                depth=0,
                first_visited_at=now,
                last_visited_at=now,
                visit_count=1,
            )
        )
        session.add(
            InventoryParameter(
                inventory_run_id=inventory_run.id,
                inventory_endpoint_id=endpoint.id,
                source_type="query",
                name="userId",
                first_seen_at=now,
                last_seen_at=now,
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
        session.flush()

        assert target.inventory_runs[0].id == inventory_run.id
        assert credential.inventory_runs[0].id == inventory_run.id
        assert auth_session.inventory_runs[0].id == inventory_run.id
        assert job.inventory_runs[0].id == inventory_run.id
        assert inventory_run.endpoints[0].path == "/demo/auth/app/api/admin/users"
