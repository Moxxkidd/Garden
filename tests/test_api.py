import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanAsset, ScanFinding, ScanRun
from app.schemas.scan import ScanFailureView, ScanRunView


@pytest.mark.parametrize(
    ("code", "snippet"),
    [
        ("overall_timeout", "不是断点续扫"),
        ("coverage_limit_reached", "请根据报告中的命中限制检查"),
        ("cross_origin_redirect_blocked", "应以该目标作为新入口单独扫描"),
        ("unknown_code", None),
        (None, None),
    ],
)
def test_scan_detail_diagnostic_guidance_is_fixed_and_escaped(app, code, snippet):
    view = ScanRunView(
        id=7,
        input_url="http://127.0.0.1/",
        normalized_url="http://127.0.0.1/",
        status="completed_with_warnings" if code else "completed",
        current_stage="finished",
        progress=100,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        finding_group_count=0,
    )
    if code:
        view.failures = [
            ScanFailureView(
                stage="collect",
                code=code,
                message="<script>TEST_SECRET</script>",
                url="http://127.0.0.1/?token=TEST_SECRET",
                retryable=False,
                attempt=1,
                occurred_at=view.created_at,
            )
        ]
    with TestClient(app) as client:
        original_service = app.state.scan_service
        app.state.scan_service = SimpleNamespace(get_scan=lambda run_id: view)
        try:
            response = client.get("/scans/7")
        finally:
            app.state.scan_service = original_service
    assert response.status_code == 200
    assert "<dd>0 类关注项，0 条原始观察</dd>" in response.text
    assert "<script>TEST_SECRET</script>" not in response.text
    if code:
        assert "&lt;script&gt;TEST_SECRET&lt;/script&gt;" in response.text
        assert code in response.text
    hints = re.findall(r'<p class="mt-1">下一步：(.*?)</p>', response.text)
    if snippet:
        assert len(hints) == 1
        assert snippet in hints[0]
        assert "TEST_SECRET" not in hints[0]
    else:
        assert not hints


def test_healthz_returns_ok(app) -> None:
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"] == "ok"


def test_index_page_loads(app) -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Garden" in response.text
    assert "Dashboard" in response.text
    assert "One URL to a structured report" in response.text
    assert "Start scan" in response.text
    assert "Authorized public + local by default" in response.text


def test_scans_page_uses_compact_navigation_and_clickable_scan_rows(app) -> None:
    scan = SimpleNamespace(
        id=4,
        normalized_url="http://127.0.0.1:3000/",
        status="completed_with_warnings",
        current_stage="finished",
        progress=100,
    )

    with TestClient(app) as client:
        original_service = app.state.scan_service
        app.state.scan_service = SimpleNamespace(list_scans=lambda: [scan])
        try:
            response = client.get("/scans")
        finally:
            app.state.scan_service = original_service

    assert response.status_code == 200
    assert 'href="/"' in response.text
    assert 'href="/scans"' in response.text
    for hidden_path in (
        "/targets",
        "/credentials",
        "/jobs",
        "/sessions",
        "/inventory",
        "/findings",
        "/evidence",
    ):
        assert f'href="{hidden_path}"' not in response.text
    assert 'data-scan-detail-url="/scans/4"' in response.text
    assert 'aria-label="View scan #4"' in response.text
    assert 'href="/scans/4"' in response.text


def test_management_pages_render(app, seeded_findings) -> None:
    target = seeded_findings["target"]
    credential = seeded_findings["credential"]
    job = seeded_findings["job"]
    auth_session = seeded_findings["auth_session"]
    inventory_run_id = seeded_findings["inventory_run_id"]
    first_finding = seeded_findings["findings"][0]
    first_evidence = first_finding.evidences[0]

    with TestClient(app) as client:
        targets_response = client.get("/targets")
        target_detail_response = client.get(f"/targets/{target.id}")
        credentials_response = client.get("/credentials")
        credential_detail_response = client.get(f"/credentials/{credential.id}")
        jobs_response = client.get("/jobs")
        job_detail_response = client.get(f"/jobs/{job.id}")
        sessions_response = client.get("/sessions")
        session_detail_response = client.get(f"/sessions/{auth_session.id}")
        inventory_response = client.get("/inventory")
        inventory_detail_response = client.get(f"/inventory/{inventory_run_id}")
        findings_response = client.get("/findings")
        finding_detail_response = client.get(f"/findings/{first_finding.id}")
        evidence_response = client.get("/evidence")
        evidence_detail_response = client.get(f"/evidence/{first_evidence.id}")

    assert targets_response.status_code == 200
    assert "local-admin" in targets_response.text
    assert target_detail_response.status_code == 200
    assert "http://localhost:8080" in target_detail_response.text
    assert "Session #1" in target_detail_response.text
    assert credentials_response.status_code == 200
    assert "admin-primary" in credentials_response.text
    assert credential_detail_response.status_code == 200
    assert "env://GARDEN_DEMO_ADMIN_PASSWORD" in credential_detail_response.text
    assert "Session #1" in credential_detail_response.text
    assert jobs_response.status_code == 200
    assert "COMPLETED" in jobs_response.text.upper()
    assert "local-admin" in jobs_response.text
    assert job_detail_response.status_code == 200
    assert "Initial management smoke job." in job_detail_response.text
    assert "admin-primary" in job_detail_response.text
    assert "Inventory Summary" in job_detail_response.text
    assert sessions_response.status_code == 200
    assert "http_cookie_jar" in sessions_response.text
    assert session_detail_response.status_code == 200
    assert "seeded-token" not in session_detail_response.text
    assert inventory_response.status_code == 200
    assert "Inventory Runs" in inventory_response.text
    assert inventory_detail_response.status_code == 200
    assert "Inventory Run" in inventory_detail_response.text
    assert findings_response.status_code == 200
    assert "Findings" in findings_response.text
    assert "new" in findings_response.text
    assert "triaged" in findings_response.text
    assert finding_detail_response.status_code == 200
    assert "Update Status" in finding_detail_response.text
    assert "Run Retest" in finding_detail_response.text
    assert "Why It Triggered" in finding_detail_response.text
    assert "First Seen" in finding_detail_response.text
    assert "Last Seen" in finding_detail_response.text
    assert "Latest Retest" in finding_detail_response.text
    assert "Linked Evidence" in finding_detail_response.text
    assert evidence_response.status_code == 200
    assert "Redacted Evidence" in evidence_response.text
    assert evidence_detail_response.status_code == 200
    assert "Structured Redacted Payload" in evidence_detail_response.text


def test_dashboard_page_shows_summary_cards(app, seeded_findings) -> None:
    with session_scope() as session:
        scan = ScanRun(
            mode="quick",
            input_url="http://127.0.0.1:3000/",
            normalized_url="http://127.0.0.1:3000/",
            status="completed_with_warnings",
            current_stage="finished",
            progress=100,
            options={},
        )
        session.add(scan)
        session.flush()
        session.add_all(
            [
                ScanAsset(
                    scan_run_id=scan.id,
                    identity_key=f"GET:/asset-{index}",
                    asset_type="page",
                    url=f"http://127.0.0.1:3000/asset-{index}",
                    method="GET",
                    attributes={},
                    discovered_at=datetime.now(timezone.utc),
                )
                for index in range(3)
            ]
        )
        session.add_all(
            [
                ScanFinding(
                    scan_run_id=scan.id,
                    dedup_key="high-confidence",
                    title="High-confidence passive finding",
                    category="security-headers",
                    severity="high",
                    confidence="high",
                    summary="Passive finding summary.",
                    remediation="Apply the documented remediation.",
                    asset_ids=[],
                    evidence_ids=[],
                    created_at=datetime.now(timezone.utc),
                ),
                ScanFinding(
                    scan_run_id=scan.id,
                    dedup_key="medium-confidence",
                    title="Medium-confidence passive finding",
                    category="information-exposure",
                    severity="low",
                    confidence="medium",
                    summary="Passive finding summary.",
                    remediation="Apply the documented remediation.",
                    asset_ids=[],
                    evidence_ids=[],
                    created_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.flush()
        scan_id = scan.id

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    for label, value in (
        ("URL Scans", 1),
        ("Assets", 3),
        ("原始关注项观察", 2),
        ("High Severity", 1),
        ("High Confidence", 1),
    ):
        assert re.search(rf">{label}</p>\s*<p[^>]*>{value}</p>", response.text)
    assert "Recent URL Scans" in response.text
    assert "Scan Status" in response.text
    assert "Category Distribution" in response.text
    assert f'href="/scans/{scan_id}"' in response.text
    assert "http://127.0.0.1:3000/" in response.text
    for legacy_content in (
        ">Targets</p>",
        ">Credential Profiles</p>",
        "Recent Jobs",
        "Findings Overview",
        "Recent Retest Outcomes",
    ):
        assert legacy_content not in response.text


def test_finding_status_update_route_updates_lifecycle_state(app, seeded_findings) -> None:
    first_finding = seeded_findings["findings"][0]

    with TestClient(app) as client:
        response = client.post(
            f"/findings/{first_finding.id}/status",
            data={"status": "triaged"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert "Status" in response.text
    assert "triaged" in response.text


@pytest.mark.parametrize("role_index", [1, 2])
@pytest.mark.parametrize(
    ("code", "snippet"),
    [
        ("authentication_session_unavailable", "登录后验证地址"),
        ("authentication_session_mismatch", "user/admin 角色"),
        ("unrecognized_auth_error", None),
    ],
)
def test_context_diagnostic_is_visible_without_report(app, role_index, code, snippet):
    from tests.test_coverage_cli import _assessment_view

    view = _assessment_view(status="incomplete", stage="finished", progress=100)
    context = view.contexts[role_index]
    context.status = "failed"
    context.error_code = code
    context.error_message = "password=TEST_SECRET"
    view.report_path = None
    view.failures = []
    with TestClient(app) as client:
        original_service = app.state.scan_service
        app.state.scan_service = SimpleNamespace(get_scan=lambda run_id: view)
        try:
            response = client.get(f"/scans/{view.id}")
        finally:
            app.state.scan_service = original_service
    assert response.status_code == 200
    assert f"{context.kind.value} / {code}" in response.text
    assert "TEST_SECRET" not in response.text
    if snippet:
        assert snippet in response.text
    else:
        assert "下一步" not in response.text
