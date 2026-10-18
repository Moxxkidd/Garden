"""Durable collection is an explicit choice in the existing submission flow."""

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_scan_preview_pages import Inputs
from tests.test_url_scan_pipeline import _service


def test_anonymous_native_preview_preserves_durable_budget_through_confirmation(tmp_path):
    sent = []

    def respond(request):
        sent.append(request.url.path)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="asset")

    scans = _service(tmp_path, respond)
    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        app.state.scan_service.shutdown()
        app.state.scan_service = scans
        form = client.get("/")
        assert 'name="durable_enabled"' in form.text
        preview = client.post(
            "/scans/preview",
            data={
                "url": "http://localhost:8111/",
                "durable_enabled": "true",
                "durable_max_requests": "7",
                "durable_max_evidence_bytes": "4096",
                "durable_min_interval_ms": "0",
            },
        )
        assert preview.status_code == 200, preview.text
        assert "可恢复增量采集" in preview.text
        assert sent == []
        fields = Inputs(preview.text).values
        confirmed = client.post("/scans/confirm", data=fields)
        assert confirmed.status_code == 303, confirmed.text
        queue = client.get(
            confirmed.headers["location"].replace("/scans/", "/api/scans/") + "/queue"
        ).json()
        assert len(queue) == 1
        assert queue[0]["options"]["max_requests"] == 7
        assert queue[0]["options"]["max_evidence_bytes"] == 4096
        assert sent == ["/"]


def test_identity_native_preview_retains_explicit_per_context_budget(seeded_records):
    from app.services.identity_collection import IdentityCollectionService
    from app.services.identity_preview import IdentityPreviewService
    from tests.test_scan_preview_pages import HoldingDispatcher

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        app.state.identity_collection.shutdown()
        collection = IdentityCollectionService(dispatcher=HoldingDispatcher())
        app.state.identity_collection = collection
        app.state.identity_preview = IdentityPreviewService(collection)
        page = client.get("/identities")
        assert 'name="durable_enabled"' in page.text
        preview = client.post(
            "/identities/preview",
            data={
                "url": seeded_records["target"].base_url,
                "target_id": seeded_records["target"].id,
                "anonymous": "on",
                "durable_enabled": "true",
                "durable_max_requests": "7",
                "durable_max_evidence_bytes": "4096",
                "durable_min_interval_ms": "0",
            },
        )
        assert preview.status_code == 200, preview.text
        assert "可恢复增量采集" in preview.text
        fields = Inputs(preview.text).values
        confirmed = client.post("/identities/start", data=fields)
        assert confirmed.status_code == 303, confirmed.text
        queue = client.get(
            confirmed.headers["location"].replace("/scans/", "/api/scans/") + "/queue"
        ).json()
        assert len(queue) == 1
        assert queue[0]["options"]["max_requests"] == 7
        assert queue[0]["options"]["max_evidence_bytes"] == 4096
        assert queue[0]["metrics"]["requests_used"] == 0


def test_formal_cli_creates_durable_queue_with_requested_limits(tmp_path, monkeypatch):
    import os
    import re
    import socket
    import subprocess
    import sys

    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    from app.cli.paths import GardenPaths
    from app.cli.web_runtime import WebRuntimeManager
    from tests.test_scan_preview_e2e import _serve

    site = FastAPI()
    sent = []

    @site.get("/", response_class=HTMLResponse)
    def home():
        sent.append("/")
        return "<p>synthetic asset</p>"

    paths = GardenPaths(tmp_path / "home")
    monkeypatch.setenv("GARDEN_HOME", str(paths.home))
    monkeypatch.setenv("GARDEN_CLI_RUNTIME", "1")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    manager = WebRuntimeManager(paths=paths, startup_timeout_seconds=15)
    with _serve(site) as base:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.cli.main",
                    "scan",
                    base,
                    "--ui-port",
                    str(port),
                    "--durable",
                    "--max-requests",
                    "7",
                    "--max-work",
                    "10",
                    "--max-evidence-bytes",
                    "4096",
                    "--max-variants",
                    "3",
                    "--origin-concurrency",
                    "2",
                    "--min-interval-ms",
                    "0",
                    "--retries",
                    "0",
                ],
                env={**os.environ, "PYTHONPATH": "."},
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            run_id = re.search(r"/scans/(\d+)", result.stdout).group(1)
            queue = httpx.get(
                f"http://127.0.0.1:{port}/api/scans/{run_id}/queue", trust_env=False
            ).json()[0]
            assert queue["options"] == {
                "max_requests": 7,
                "max_work": 10,
                "max_evidence_bytes": 4096,
                "max_variants": 3,
                "origin_concurrency": 2,
                "min_interval_ms": 0,
                "retry_attempts": 0,
            }
            assert queue["metrics"]["requests_used"] == 1
            assert sent == ["/"]
        finally:
            manager.stop()


def test_fixed_three_context_preview_rejects_durable_mode():
    from app.core.settings import get_settings
    from app.db.bootstrap import session_scope
    from app.schemas.assessment import AssessmentStartRequest
    from app.schemas.collection_queue import CollectionOptions
    from app.schemas.scan import ScanOptions
    from app.services.scan_submission_preview import build_preview
    from tests.test_coverage_wizard import _setup_records

    setup = _setup_records()
    request = AssessmentStartRequest(
        url=setup.url,
        mode="authenticated_coverage",
        target_id=setup.target_id,
        user_profile_id=setup.user_id,
        admin_profile_id=setup.admin_id,
        options=ScanOptions(durable=CollectionOptions()),
    )
    with session_scope() as session:
        preview = build_preview(session, request, get_settings())
    assert not preview.can_submit
    assert any(issue.code == "durable_mode_unsupported" for issue in preview.issues)
