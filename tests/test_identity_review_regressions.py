"""Final review regressions: live identity proof and evidence association."""

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.core.errors import InputValidationError
from app.db.bootstrap import session_scope
from app.models.scan_run import ScanAsset, ScanRun
from app.models.target import Target
from app.schemas.assets import AssetRecord
from app.schemas.identity import (
    IdentityCollectionRequest,
    IdentityContextView,
    ManualLoginRequest,
    SessionHealthView,
)
from app.schemas.scan import ScanOptions
from app.services.identity_collection import IdentityCollectionService
from app.services.identity_matrix import build_identity_matrix
from app.services.identity_sessions import IdentitySessionService
from app.services.scan_application import InlineScanDispatcher
from app.services.scan_reporting import ScanReportService
from app.services.session_storage import SessionStorageService
from tests.test_identity_recovery import checkpoint_fixture
from tests.test_scan_preview_e2e import _serve


def test_changed_same_origin_target_invalidates_recovery(seeded_records, tmp_path):
    service, request = checkpoint_fixture(seeded_records, tmp_path)
    with session_scope() as session:
        service.preview(session, request)
        target = session.get(Target, seeded_records["target"].id)
        target.base_url = target.base_url.rstrip("/") + "/different-app"
        session.flush()
        with pytest.raises(InputValidationError):
            service.preview(session, request)


def test_matrix_keeps_confirmed_200_separate_from_uncertain_401():
    row = AssetRecord(
        asset_id="scan:1:asset:1",
        record_id=1,
        record_type="scan_asset",
        kind="page",
        original_type="page",
        url="https://example.test/asset",
        site="https://example.test",
        method="GET",
        status_codes=[401],
        observation="response_observed",
        context="profile:1",
        identity_assessment="confirmed",
        evidence_ids=[10, 11],
        identity_observations=[
            {"evidence_id": 10, "status_code": 200, "assessment": "confirmed"},
            {"evidence_id": 11, "status_code": 401, "assessment": "identity_uncertain"},
        ],
        provenance_note="test",
        source_url="/scans/1",
    )
    context = IdentityContextView(
        context_id=1,
        context_key="profile:1",
        profile_id=1,
        display_name="reader",
        health=SessionHealthView(status="unknown"),
        completeness="incomplete",
    )
    cell = build_identity_matrix([row], [context]).rows[0].cells["profile:1"]
    assert cell.state == "observed"
    assert cell.status_codes == [200]
    assert cell.evidence_ids == [10]
    assert cell.uncertain_status_codes == [401]
    assert cell.uncertain_evidence_ids == [11]


@pytest.mark.parametrize("storage_kind", ["cookie", "localStorage", "sessionStorage"])
def test_live_cookie_removal_is_not_confirmed_by_old_saved_cookie(
    seeded_records, tmp_path, storage_kind
):
    site = FastAPI()

    @site.get("/{path:path}")
    def page(path: str, request: Request):
        authenticated = request.cookies.get("identity") == "valid"
        if path == "proof" and storage_kind != "cookie":
            return HTMLResponse(
                f"<body><script>if ({storage_kind}.getItem('identity') === 'valid') "
                "document.body.innerHTML='<b id=identity>reader</b>';</script></body>"
            )
        if path == "start" and storage_kind != "cookie":
            return HTMLResponse(
                f"<script>{storage_kind}.clear()</script><a href='/public'>Public</a>"
            )
        if path == "proof":
            return HTMLResponse('<b id="identity">reader</b>' if authenticated else "Login")
        response = HTMLResponse('<a href="/public">Public</a>')
        if path == "start":
            response.delete_cookie("identity")
        return response

    with _serve(site) as base:
        states = IdentitySessionService(storage=SessionStorageService(tmp_path / "states"))
        with session_scope() as session:
            session.get(Target, seeded_records["target"].id).base_url = base
            profile_id = seeded_records["credential"].id
            raw = {"cookies": [], "origins": []}
            if storage_kind == "cookie":
                raw["cookies"] = [
                    {"name": "identity", "value": "valid", "domain": "127.0.0.1", "path": "/"}
                ]
            elif storage_kind == "localStorage":
                raw["origins"] = [
                    {"origin": base, "localStorage": [{"name": "identity", "value": "valid"}]}
                ]
            else:
                raw = {
                    "version": 1,
                    "target_origin": base,
                    "storage_state": raw,
                    "session_storage": {base: {"identity": "valid"}},
                }
            health = states.import_state(
                session,
                profile_id,
                json.dumps(raw),
                ManualLoginRequest(
                    profile_id=profile_id,
                    login_url=base + "/login",
                    validate_url=base + "/proof",
                    success_selector="#identity",
                ),
            )
            assert health.status == "ready"
        service = IdentityCollectionService(
            sessions=states,
            dispatcher=InlineScanDispatcher(),
            report_service=ScanReportService(tmp_path / "reports"),
        )
        with session_scope() as session:
            run_id = service.start(
                session,
                IdentityCollectionRequest(
                    url=base + "/start",
                    target_id=seeded_records["target"].id,
                    profile_ids=[profile_id],
                    options=ScanOptions(render_wait_ms=0),
                ),
            )
        with session_scope() as session:
            run = session.get(ScanRun, run_id)
            assert run.contexts[0].health_status != "ready"
            assert all(
                a.attributes["identity_assessment"] != "confirmed"
                for a in session.scalars(select(ScanAsset).where(ScanAsset.scan_run_id == run_id))
            )
            assert run.options["collection_mode"] == "browser"
            assert next(s for s in run.stages if s.name == "analyze").status == "skipped"


def test_identity_summary_reports_bounded_complete_and_skipped_analysis():
    from app.services.scan_result_presentation import build_result_summary

    summary = build_result_summary(
        status="completed",
        completeness="complete",
        mode="identity_collection",
        raw_count=0,
        group_count=0,
    )
    assert "完整性未知" not in summary.coverage
    assert "所选身份" in summary.coverage
    assert "未执行" in summary.findings
