"""Late network responses must not escape the final identity proof."""

import asyncio
import json
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.assets import AssetQuery
from app.schemas.identity import IdentityCollectionRequest, ManualLoginRequest
from app.schemas.scan import ScanOptions
from app.services.asset_catalog import AssetCatalogService
from app.services.identity_collection import IdentityCollectionService
from app.services.identity_sessions import IdentitySessionService
from app.services.scan_application import InlineScanDispatcher, ScanApplicationService
from app.services.scan_reporting import ScanReportService
from app.services.session_storage import SessionStorageService
from tests.test_scan_preview_e2e import _serve


@pytest.mark.parametrize("settle_timeout", [False, True])
def test_response_finishing_during_final_identity_proof_is_not_left_uncertain(
    seeded_records, tmp_path, settle_timeout, monkeypatch
):
    # Header retrieval yields to Playwright's event loop. Hold this callback
    # across a settle-loop tick so finalization cannot outrun persistence.
    from playwright.sync_api import Response

    original_headers = Response.all_headers

    def delayed_headers(response):
        if not settle_timeout and response.url.endswith("/asset"):
            response.request.frame.page.wait_for_timeout(100)
        return original_headers(response)

    monkeypatch.setattr(Response, "all_headers", delayed_headers)
    release = threading.Event()
    body_started = threading.Event()
    calls = []
    site = FastAPI()

    @site.get("/", response_class=HTMLResponse)
    def home():
        return '<link rel="icon" href="data:,"><script>fetch("/asset")</script>'

    @site.get("/asset", response_class=HTMLResponse)
    async def asset():
        body_started.set()
        await asyncio.to_thread(release.wait, 5)
        return "<p>synthetic asset</p>"

    def verify(state, config, before_request):
        calls.append(True)
        if len(calls) == 3:  # Import, initial proof, final proof.
            assert body_started.wait(2)
            release.set()
            time.sleep(0.1)
        return True

    with _serve(site) as base:
        states = IdentitySessionService(
            storage=SessionStorageService(tmp_path / "states"), verifier=verify
        )
        with session_scope() as session:
            target = session.get(Target, seeded_records["target"].id)
            target.base_url = base
            target_id = target.id
            profile_id = seeded_records["credential"].id
            states.import_state(
                session,
                profile_id,
                json.dumps({"cookies": [], "origins": []}),
                ManualLoginRequest(
                    profile_id=profile_id,
                    login_url=base + "/login",
                    validate_url=base + "/verify",
                    success_selector="#identity",
                ),
            )
        service = IdentityCollectionService(
            sessions=states,
            dispatcher=InlineScanDispatcher(),
            report_service=ScanReportService(tmp_path / "reports"),
        )
        reader = ScanApplicationService(dispatcher=InlineScanDispatcher())
        try:
            with session_scope() as session:
                run_id = service.start(
                    session,
                    IdentityCollectionRequest(
                        url=base,
                        target_id=target_id,
                        profile_ids=[profile_id],
                        options=ScanOptions(
                            max_pages=10,
                            render_wait_ms=100,
                            request_timeout_seconds=1 if settle_timeout else 15,
                        ),
                    ),
                )
            result = reader.get_scan(run_id)
            with session_scope() as session:
                catalog = AssetCatalogService().list(
                    session, AssetQuery(source="scan", run_id=run_id)
                )
            assets = catalog.items
            if settle_timeout:
                assert result.completeness == "incomplete"
                assert result.contexts[0].error_code == "response_settle_timeout"
                assert not any(a.url.endswith("/asset") for a in assets)
            else:
                assert any(a.url.endswith("/asset") for a in assets)
                assert result.completeness == "complete"
            assert all(a.identity_assessment == "confirmed" for a in assets)
            assert catalog.scope.contexts[0].health_status == "ready"
            assert result.contexts[0].session_validation_status == "completed"
        finally:
            release.set()
            service.shutdown()
            reader.shutdown()
