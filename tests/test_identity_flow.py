"""Continuous identity guidance from existing login and restoration facts."""

from datetime import datetime, timedelta, timezone

from app.db.bootstrap import session_scope
from app.models.auth_session import AuthSession
from app.models.login_attempt import LoginAttempt
from app.services.identity_flow import identity_flow


def test_failed_replacement_keeps_effective_session_and_entry_guidance(seeded_records):
    with session_scope() as session:
        profile = seeded_records["credential"]
        view = identity_flow(session, profile.id)
        assert view["state"] == "unknown"  # A legacy cookie record is not fresh restoration proof.
        old = session.get(AuthSession, seeded_records["auth_session"].id)
        old.identity_metadata = {"version": 1}
        old.last_validated_at = datetime.now(timezone.utc)
        old.status = "active"
        session.flush()
        assert identity_flow(session, profile.id)["state"] == "ready"
        session.add(
            LoginAttempt(
                profile_id=profile.id,
                state="failed",
                owner_token="fixture",
                configuration={},
                reason_code="restore_not_verified",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
        )
        session.flush()
        view = identity_flow(session, profile.id)
        assert view["state"] == "ready"
        assert view["replacement"]["reason_code"] == "restore_not_verified"
        assert view["session_id"] == old.id
        assert view["checked_at"] is not None
        old.status = "invalid"
        session.flush()
        assert identity_flow(session, profile.id)["state"] == "invalid"


def test_entry_guidance_does_not_bind_independent_identity_to_project_lifetime(
    seeded_records, tmp_path
):
    import json

    from app.models.target import Target
    from app.schemas.identity import ManualLoginRequest
    from app.services.identity_sessions import IdentitySessionService
    from app.services.projects import ProjectService
    from app.services.session_storage import SessionStorageService
    from tests.helpers.project_assets import project_fixture

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        service = IdentitySessionService(
            storage=SessionStorageService(tmp_path / "private"), verifier=lambda *_: True
        )
        proof = ManualLoginRequest(
            profile_id=seeded_records["credential"].id,
            project_entry_id=entry.id,
            login_url=target.base_url + "login",
            validate_url=target.base_url + "me",
            success_text="reader",
        )
        result = service.import_state(
            session, proof.profile_id, json.dumps({"cookies": [], "origins": []}), proof
        )
        ProjectService().archive(session, project.id, project.revision)
        assert service.validate(session, result.session_id).status == "ready"
