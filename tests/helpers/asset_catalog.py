"""Unified list contracts: provenance and filtering must survive lossy display redaction."""

from datetime import datetime, timezone

import pytest

from app.db.bootstrap import session_scope
from app.models.inventory_endpoint import InventoryEndpoint
from app.models.inventory_page import InventoryPage
from app.models.inventory_run import InventoryRun
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanAsset, ScanEvidence, ScanRun

NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


@pytest.fixture
def asset_scan():
    with session_scope() as session:
        run = ScanRun(
            input_url="https://site.test/",
            normalized_url="https://site.test/",
            status="incomplete",
            mode="authenticated_coverage",
            completeness="missing_admin_context",
        )
        session.add(run)
        session.flush()
        user = ScanContext(
            scan_run_id=run.id,
            kind="user",
            status="completed",
            completeness="complete",
            collection_status="completed",
        )
        admin = ScanContext(
            scan_run_id=run.id,
            kind="admin",
            status="failed",
            completeness="incomplete",
            collection_status="failed",
        )
        session.add_all([user, admin])
        session.flush()
        rows = [
            ScanAsset(
                scan_run_id=run.id,
                context_id=user.id,
                asset_type="page",
                method="GET",
                url="https://name:URL_PASSWORD@site.test/a?token=QUERY_SECRET#FRAGMENT_SECRET",
                title="=1+1",
                status_code=200,
                discovered_at=NOW,
                attributes={"content_type": "text/html", "password": "ATTRIBUTE_SECRET"},
            ),
            ScanAsset(
                scan_run_id=run.id,
                context_id=user.id,
                asset_type="page",
                method="GET",
                url="https://site.test/a?token=OTHER_SECRET",
                status_code=403,
                discovered_at=NOW,
            ),
            ScanAsset(
                scan_run_id=run.id,
                context_id=admin.id,
                asset_type="endpoint",
                method="POST",
                url="https://site.test/api",
                status_code=None,
                discovered_at=NOW,
            ),
            ScanAsset(
                scan_run_id=run.id,
                asset_type="script",
                method="GET",
                url="https://site.test/app.js",
                status_code=200,
                discovered_at=NOW,
            ),
            ScanAsset(
                scan_run_id=run.id,
                asset_type="document",
                method="GET",
                url="https://site.test/manual.pdf",
                status_code=200,
                discovered_at=NOW,
            ),
        ]
        session.add_all(rows)
        session.flush()
        evidence = ScanEvidence(
            scan_run_id=run.id,
            asset_id=rows[0].id,
            evidence_type="http",
            title="sample",
            source_url=rows[0].url,
            summary="RESPONSE_SECRET",
            data={"body": "BODY_SECRET"},
            collected_at=NOW,
        )
        session.add(evidence)
        session.flush()
        return run.id, rows[0].id, evidence.id


@pytest.fixture
def asset_inventory(seeded_records):
    with session_scope() as session:
        run = InventoryRun(
            target_id=seeded_records["target"].id,
            credential_profile_id=seeded_records["credential"].id,
            auth_session_id=seeded_records["auth_session"].id,
            scan_job_id=seeded_records["job"].id,
            status="completed",
            started_from_url="http://localhost:8080/?token=START_SECRET",
            pages_count=900,
            endpoints_count=900,
        )
        session.add(run)
        session.flush()
        session.add(
            InventoryPage(
                inventory_run_id=run.id,
                url="http://localhost:8080/a?x=PAGE_SECRET",
                title="A",
                status_code=None,
                first_visited_at=NOW,
                last_visited_at=NOW,
                visit_count=2,
            )
        )
        session.add(
            InventoryEndpoint(
                inventory_run_id=run.id,
                method="GET",
                path="/api",
                url="http://localhost:8080/api?x=ENDPOINT_SECRET",
                first_seen_at=NOW,
                last_seen_at=NOW,
                request_count=3,
                status_codes_observed=[200, 401],
                content_type="application/json",
            )
        )
        return run.id
