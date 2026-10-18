"""Identity capture must budget its post-processing before saving request material."""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.collection_queue import CollectionOptions
from app.schemas.identity import IdentityCollectionRequest
from app.schemas.scan import ScanOptions
from app.services.collection_queue import CollectionQueueService
from app.services.identity_collection import IdentityCollectionService
from app.services.identity_sessions import IdentitySessionService
from app.services.scan_application import InlineScanDispatcher
from app.services.scan_reporting import ScanReportService
from app.services.session_storage import SessionStorageService
from tests.test_scan_preview_e2e import _serve
from tests.test_url_scan_pipeline import _service


def test_identity_request_material_cannot_bypass_evidence_budget(seeded_records, tmp_path):
    site = FastAPI()

    @site.get("/", response_class=HTMLResponse)
    def home():
        return '<link rel="icon" href="data:,"><p>synthetic observation</p>'

    storage = SessionStorageService(tmp_path / "identity-material")
    with _serve(site) as base:
        baseline = _service(tmp_path, lambda request: None)
        identities = IdentityCollectionService(
            sessions=IdentitySessionService(storage=storage),
            dispatcher=InlineScanDispatcher(),
            report_service=ScanReportService(tmp_path / "identity-reports"),
        )
        try:
            run = baseline.start_scan(
                base,
                ScanOptions(
                    collection_mode="browser",
                    render_wait_ms=0,
                    max_pages=1,
                    durable=CollectionOptions(max_requests=1, min_interval_ms=0),
                ),
            )
            with session_scope() as session:
                base_bytes = CollectionQueueService().for_run(session, run.id)[0]["metrics"][
                    "evidence_bytes"
                ]
                assert base_bytes > 0
                target = session.get(Target, seeded_records["target"].id)
                target.base_url = base
                target_id = target.id
            with session_scope() as session:
                run_id = identities.start(
                    session,
                    IdentityCollectionRequest(
                        url=base,
                        target_id=target_id,
                        include_anonymous=True,
                        options=ScanOptions(
                            render_wait_ms=0,
                            max_pages=1,
                            durable=CollectionOptions(
                                max_requests=1, min_interval_ms=0, max_evidence_bytes=base_bytes + 1
                            ),
                        ),
                    ),
                )
            result = baseline.get_scan(run_id)
            with session_scope() as session:
                queue = CollectionQueueService().for_run(session, run_id)[0]
                assert queue["metrics"]["requests_used"] == 1
                assert queue["metrics"]["responses"] == 1
                assert queue["metrics"]["evidence_bytes"] <= base_bytes + 1
                assert not queue["coverage_complete"]
                assert queue["counts"].get("observed", 0) == 0
            assert result.asset_count == 0
            assert not list(
                (tmp_path / "identity-material" / "requests").rglob(
                    f"context-{queue['context_id']}-*.json"
                )
            )
            assert result.contexts[0].error_code == "collection_evidence_budget"
        finally:
            identities.shutdown()
            baseline.shutdown()
