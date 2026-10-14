import json

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.collection_queue import CollectionOptions
from app.schemas.identity import IdentityCollectionRequest, ManualLoginRequest
from app.schemas.scan import ScanOptions
from app.services.collection_queue import CollectionQueueService
from app.services.identity_collection import IdentityCollectionService
from app.services.identity_sessions import IdentitySessionService
from app.services.scan_application import InlineScanDispatcher
from app.services.scan_reporting import ScanReportService
from app.services.session_storage import SessionStorageService
from tests.test_scan_preview_e2e import _serve


@pytest.mark.parametrize("redirect_validation, budget", [(False, None), (True, None), (False, 2)])
def test_identity_health_and_business_share_durable_request_budget(
    seeded_records, tmp_path, redirect_validation, budget
):
    site = FastAPI()
    seen = []

    @site.get("/{path:path}", response_class=HTMLResponse)
    def page(path: str, request: Request):
        seen.append(path)
        if path == "verify" and redirect_validation:
            return RedirectResponse("/verified", status_code=302)
        assert request.cookies.get("fixture") == "reader"
        return '<link rel="icon" href="data:,"><p id="identity">reader</p>'

    expected_requests = 5 if redirect_validation else 3
    with _serve(site) as base:
        states = IdentitySessionService(storage=SessionStorageService(tmp_path / "states"))
        with session_scope() as session:
            target = session.get(Target, seeded_records["target"].id)
            target.base_url = base
            profile_id = seeded_records["credential"].id
            states.import_state(
                session,
                profile_id,
                json.dumps(
                    {
                        "cookies": [
                            {
                                "name": "fixture",
                                "value": "reader",
                                "domain": "127.0.0.1",
                                "path": "/",
                            }
                        ],
                        "origins": [],
                    }
                ),
                ManualLoginRequest(
                    profile_id=profile_id,
                    login_url=base + "/login",
                    validate_url=base + "/verify",
                    success_selector="#identity",
                ),
            )
            target_id = target.id
            from app.schemas.projects import ProjectEntryCreate
            from app.services.projects import ProjectService

            projects = ProjectService()
            project = projects.create(session, "Incremental identity")
            entry = projects.add_entry(
                session,
                project.id,
                ProjectEntryCreate(target_id=target_id, entry_url=base + "/asset", name="Asset"),
            )
            project_id, entry_id = project.id, entry.id
        seen.clear()
        service = IdentityCollectionService(
            sessions=states,
            dispatcher=InlineScanDispatcher(),
            report_service=ScanReportService(tmp_path / "reports"),
        )
        with session_scope() as session:
            run_id = service.start(
                session,
                IdentityCollectionRequest(
                    url=base + "/asset",
                    target_id=target_id,
                    project_entry_id=entry_id,
                    profile_ids=[profile_id],
                    options=ScanOptions(
                        render_wait_ms=100,
                        durable=CollectionOptions(
                            max_requests=budget or expected_requests, min_interval_ms=0
                        ),
                    ),
                ),
            )
        with session_scope() as session:
            queues = CollectionQueueService().for_run(session, run_id)
            assert len(queues) == 1
            metrics = queues[0]["metrics"]
            assert metrics["requests_used"] == len(seen) == (budget or expected_requests)
            assert metrics["by_kind"] == {
                "identity_health": (budget or expected_requests) - 1,
                "browser": 1,
            }
            if budget:
                assert not queues[0]["coverage_complete"]
                assert queues[0]["counts"]["uncertain"] == 1
            else:
                assert queues[0]["coverage_complete"]

        if budget:
            from app.schemas.collection_resume import CollectionResumeRequest
            from app.services.collection_resume import CollectionResumeService

            resume = CollectionResumeService(identities=service)
            request = CollectionResumeRequest(
                queue_id=queues[0]["id"],
                options=CollectionOptions(max_requests=3, min_interval_ms=0),
            )
            with session_scope() as session:
                preview = resume.preview(session, request)
            seen.clear()
            with session_scope() as session:
                child_id = resume.start(session, request, preview["token"])
            with session_scope() as session:
                child = resume.queues.for_run(session, child_id)[0]
                assert child["coverage_complete"]
                assert child["metrics"]["requests_used"] == len(seen) == 3

        if not budget and not redirect_validation:
            from app.schemas.project_incremental import ProjectIncrementalRequest
            from app.services.project_incremental import ProjectIncrementalService

            planner = ProjectIncrementalService(identities=service)
            request = ProjectIncrementalRequest(
                project_id=project_id,
                entry_id=entry_id,
                profile_id=profile_id,
                stale_after_days=0,
                options=CollectionOptions(min_interval_ms=0),
            )
            with session_scope() as session:
                preview = planner.preview(session, request)
                assert preview["work_count"] == 1
                assert preview["identity_recheck_required"]
            seen.clear()
            with session_scope() as session:
                child_id = planner.start(session, request, preview["token"])
            assert seen == ["verify", "asset", "verify"]
            with session_scope() as session:
                assert planner.start(session, request, preview["token"]) == child_id
                child = planner.queues.for_run(session, child_id)[0]
                assert child["coverage_complete"]
            assert len(seen) == 3
        service.shutdown()
