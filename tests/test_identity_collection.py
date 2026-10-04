import json

from sqlalchemy import select

from app.db.bootstrap import session_scope
from app.models.credential_profile import CredentialProfile
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanAsset, ScanRun
from app.models.target import Target
from app.schemas.identity import IdentityCollectionRequest, ManualLoginRequest
from app.schemas.scan import ScanOptions
from app.services.identity_collection import IdentityCollectionService
from app.services.identity_sessions import IdentitySessionService
from app.services.scan_application import InlineScanDispatcher
from app.services.scan_reporting import ScanReportService
from app.services.session_storage import SessionStorageService
from tests.fixtures.identity_site import identity_site
from tests.test_scan_preview_e2e import _serve


def test_single_user_and_two_same_role_identities_are_isolated(seeded_records, tmp_path):
    fixture, state = identity_site()
    with _serve(fixture) as base:
        states = IdentitySessionService(
            storage=SessionStorageService(tmp_path / "states"), verifier=lambda *args: True
        )
        with session_scope() as session:
            target = session.get(Target, seeded_records["target"].id)
            target.base_url = base
            one = session.get(CredentialProfile, seeded_records["credential"].id)
            one.role = "reader"
            two = CredentialProfile(
                target_id=target.id,
                name="second",
                role="reader",
                auth_type="cookie",
                username="second",
                secret_ref="unused",
                login_config_path="unused",
            )
            session.add(two)
            session.flush()
            ids = [one.id, two.id]
            for profile, who in zip(ids, ["reader-one", "reader-two"], strict=True):
                state["sessions"][who] = who
                states.import_state(
                    session,
                    profile,
                    json.dumps(
                        {
                            "cookies": [
                                {
                                    "name": "identity",
                                    "value": who,
                                    "domain": "127.0.0.1",
                                    "path": "/",
                                }
                            ],
                            "origins": [],
                        }
                    ),
                    ManualLoginRequest(
                        profile_id=profile,
                        login_url=base + "/login",
                        validate_url=base + "/me",
                        success_selector="#identity",
                    ),
                )
        service = IdentityCollectionService(
            sessions=states,
            dispatcher=InlineScanDispatcher(),
            report_service=ScanReportService(tmp_path / "reports"),
        )
        for profiles in [ids[:1], ids]:
            with session_scope() as session:
                run_id = service.start(
                    session,
                    IdentityCollectionRequest(
                        url=base + "/me",
                        target_id=target.id,
                        profile_ids=profiles,
                        options=ScanOptions(max_pages=10, max_resources=10, render_wait_ms=0),
                    ),
                )
            with session_scope() as session:
                run = session.get(ScanRun, run_id)
                assert run.status == "completed", (
                    run.error_code,
                    [(c.error_code, c.error_message) for c in run.contexts],
                )
                contexts = session.scalars(
                    select(ScanContext).where(ScanContext.scan_run_id == run_id)
                ).all()
                assert {c.context_key for c in contexts} == {f"profile:{p}" for p in profiles}
                assets = session.scalars(
                    select(ScanAsset).where(ScanAsset.scan_run_id == run_id)
                ).all()
                assert {a.context_id for a in assets} == {c.id for c in contexts}
                assert all(a.attributes["identity_assessment"] == "confirmed" for a in assets)
                assert run.report_path
        assert {who for path, who in state["requests"] if path == "asset"} == {
            "reader-one",
            "reader-two",
        }


def test_anonymous_only_needs_no_credentials(seeded_records, tmp_path):
    fixture, _ = identity_site()
    with _serve(fixture) as base:
        with session_scope() as session:
            target = session.get(Target, seeded_records["target"].id)
            target.base_url = base
        service = IdentityCollectionService(
            dispatcher=InlineScanDispatcher(),
            report_service=ScanReportService(tmp_path / "reports"),
        )
        with session_scope() as session:
            run_id = service.start(
                session,
                IdentityCollectionRequest(
                    url=base,
                    target_id=target.id,
                    include_anonymous=True,
                    options=ScanOptions(max_pages=1, render_wait_ms=0),
                ),
            )
        with session_scope() as session:
            run = session.get(ScanRun, run_id)
            assert run.status in {"completed", "completed_with_warnings"}
            assert [c.context_key for c in run.contexts] == ["anonymous"]


def test_cross_target_profile_rejected_before_dispatch(seeded_records):
    import pytest

    from app.core.errors import InputValidationError

    class NoDispatch:
        def submit(self, *args):
            pytest.fail("invalid identity must never dispatch")

    with session_scope() as session:
        other = Target(
            name="other",
            base_url="https://other.example",
            status="active",
            type="web",
            owner="test",
        )
        session.add(other)
        session.flush()
        with pytest.raises(InputValidationError):
            IdentityCollectionService(dispatcher=NoDispatch()).start(
                session,
                IdentityCollectionRequest(
                    url=other.base_url,
                    target_id=other.id,
                    profile_ids=[seeded_records["credential"].id],
                ),
            )


def test_all_discovery_sources_share_identity_and_budget(seeded_records, tmp_path):
    from fastapi import FastAPI, Request
    from fastapi.responses import Response

    fixture = FastAPI()
    received = []

    @fixture.get("/{path:path}")
    def page(path: str, request: Request):
        received.append((path, request.cookies.get("identity")))
        bodies = {
            "": (
                '<a href="/html">HTML</a><a href="#/view">View</a>'
                '<script src="/app.js"></script>'
                '<script>fetch("/dynamic");</script>',
                "text/html",
            ),
            "app.js": (
                'function declared() { return fetch("/unrequested-api"); }',
                "application/javascript",
            ),
            "sitemap.xml": (
                f"<urlset><url><loc>{str(request.base_url)}mapped</loc></url></urlset>",
                "application/xml",
            ),
        }
        body, kind = bodies.get(path, ("ok", "text/plain"))
        return Response(body, media_type=kind)

    with _serve(fixture) as base:
        states = IdentitySessionService(
            storage=SessionStorageService(tmp_path / "states"), verifier=lambda *args: True
        )
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
                                "name": "identity",
                                "value": "same-account",
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
                    validate_url=base,
                    success_text="ok",
                ),
            )
        service = IdentityCollectionService(
            sessions=states,
            dispatcher=InlineScanDispatcher(),
            report_service=ScanReportService(tmp_path / "reports"),
        )
        with session_scope() as session:
            run_id = service.start(
                session,
                IdentityCollectionRequest(
                    url=base,
                    target_id=target.id,
                    profile_ids=[profile_id],
                    options=ScanOptions(
                        sitemap_enabled=True,
                        js_enabled=True,
                        seed_input=base + "/imported",
                        render_wait_ms=100,
                        max_browser_requests=30,
                    ),
                ),
            )
        with session_scope() as session:
            run = session.get(ScanRun, run_id)
            assert run.status == "completed", [(c.error_code) for c in run.contexts]
            route_assets = session.scalars(
                select(ScanAsset).where(
                    ScanAsset.scan_run_id == run_id, ScanAsset.status_code.is_(None)
                )
            ).all()
            assert route_assets
            assert all(a.attributes["identity_assessment"] == "confirmed" for a in route_assets)
            snapshot = run.contexts[0].identity_snapshot["discovery"]
            assert {"html_link", "sitemap", "js_literal", "url_import", "hash_route"} <= set(
                snapshot["stats"]
            )
        paths = {path for path, _ in received}
        assert {"html", "mapped", "imported", "dynamic", "app.js"} <= paths
        assert "unrequested-api" not in paths
        assert {who for _, who in received} == {"same-account"}
        assert len(received) <= 30


def test_real_expiry_preserves_confirmed_batches_and_budget(seeded_records, tmp_path):
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, RedirectResponse

    fixture = FastAPI()
    active = [True]
    received = []

    @fixture.get("/{path:path}")
    def page(path: str, request: Request):
        received.append(path)
        if path == "login":
            return HTMLResponse("Please sign in")
        if request.cookies.get("identity") != "valid" or not active[0]:
            return RedirectResponse("/login", status_code=302)
        if path == "proof":
            return HTMLResponse('<b id="identity">Authenticated</b>')
        if path == "denied":
            return HTMLResponse("Forbidden", status_code=403)
        if path == "start":
            return HTMLResponse("".join(f'<a href="/item/{i}">{i}</a>' for i in range(30)))
        if path == "item/21":
            active[0] = False
            return RedirectResponse("/login", status_code=302)
        return HTMLResponse("Protected business data")

    with _serve(fixture) as base:
        states = IdentitySessionService(storage=SessionStorageService(tmp_path / "states"))
        with session_scope() as session:
            target = session.get(Target, seeded_records["target"].id)
            target.base_url = base
            profile_id = seeded_records["credential"].id
            health = states.import_state(
                session,
                profile_id,
                json.dumps(
                    {
                        "cookies": [
                            {
                                "name": "identity",
                                "value": "valid",
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
        for entry, budget in [("denied", 30), ("start", 2), ("start", 100)]:
            received.clear()
            with session_scope() as session:
                run_id = service.start(
                    session,
                    IdentityCollectionRequest(
                        url=base + "/" + entry,
                        target_id=target.id,
                        profile_ids=[profile_id],
                        options=ScanOptions(
                            render_wait_ms=0, max_pages=100, max_browser_requests=budget
                        ),
                    ),
                )
            with session_scope() as session:
                run = session.get(ScanRun, run_id)
                context = run.contexts[0]
                assets = session.scalars(
                    select(ScanAsset).where(ScanAsset.context_id == context.id)
                ).all()
                assert all(not a.url.endswith("/proof") for a in assets)
                assert len(received) <= budget
                if entry == "denied":
                    assert context.health_status == "ready", context.error_code
                    assert any(
                        a.status_code == 403 and a.attributes["identity_assessment"] == "confirmed"
                        for a in assets
                    )
                elif budget == 2:
                    assert context.error_code == "max_browser_requests"
                    assert context.completeness == "incomplete"
                    assert all(
                        a.attributes["identity_assessment"] == "identity_uncertain" for a in assets
                    )
                else:
                    assert context.health_status != "ready"
                    assert context.completeness == "incomplete"
                    assessments = {a.attributes["identity_assessment"] for a in assets}
                    assert "confirmed" in assessments
                    assert "identity_uncertain" in assessments
                    assert "item/29" not in received
