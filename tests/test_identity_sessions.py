import json
import stat

import pytest

from app.core.errors import InputValidationError
from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.identity import ManualLoginRequest
from app.services.identity_sessions import IdentitySessionService, parse_identity_state
from app.services.session_storage import SessionStorageService
from tests.fixtures.identity_site import identity_site
from tests.test_scan_preview_e2e import _serve


def test_import_requires_positive_restore_proof(seeded_records, tmp_path):
    fixture, state = identity_site()
    with _serve(fixture) as base:
        state["sessions"]["PRIVATE_TOKEN"] = "reader"
        service = IdentitySessionService(storage=SessionStorageService(tmp_path / "states"))
        profile_id = seeded_records["credential"].id
        config = ManualLoginRequest(
            profile_id=profile_id,
            login_url=base + "/login",
            validate_url=base + "/me",
            success_selector="#identity",
        )
        payload = {
            "cookies": [
                {
                    "name": "identity",
                    "value": "PRIVATE_TOKEN",
                    "domain": "127.0.0.1",
                    "path": "/",
                    "httpOnly": True,
                    "secure": False,
                    "sameSite": "Lax",
                }
            ],
            "origins": [],
        }
        with session_scope() as session:
            session.get(Target, seeded_records["target"].id).base_url = base
            result = service.import_state(session, profile_id, json.dumps(payload), config)
            assert result.status == "ready" and result.session_id
            assert "PRIVATE_TOKEN" not in result.model_dump_json()
            saved = service.load_for_collection(session, result.session_id)
            assert saved.storage_state["cookies"][0]["value"] == "PRIVATE_TOKEN"
            service.revoke(session, result.session_id)
            with pytest.raises(InputValidationError):
                service.load_for_collection(session, result.session_id)
            payload["cookies"] = []
            assert (
                service.import_state(session, profile_id, json.dumps(payload), config).status
                == "validation_failed"
            )
    assert stat.S_IMODE((tmp_path / "states").stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(p.stat().st_mode) == 0o600 for p in (tmp_path / "states").glob("*.json")
    )


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "x" * (1048576 + 1),
        json.dumps(
            {
                "cookies": [{"name": "n", "value": "s", "domain": "evil.test", "path": "/"}],
                "origins": [],
            }
        ),
        json.dumps(
            {"cookies": [], "origins": [{"origin": "https://evil.test", "localStorage": []}]}
        ),
        json.dumps({"cookies": [], "origins": [], "unsupported": True}),
    ],
)
def test_import_rejects_invalid_or_outside_state(raw):
    with pytest.raises(InputValidationError):
        parse_identity_state(raw, "https://site.test")


def test_private_storage_read_cannot_escape_root(tmp_path):
    storage = SessionStorageService(tmp_path / "states")
    outside = tmp_path / "secret.json"
    outside.write_text('{"value":"SECRET"}')
    with pytest.raises(InputValidationError):
        storage.read_identity_payload(str(outside))


def test_failed_write_has_no_ready_session_or_partial_file(seeded_records, tmp_path, monkeypatch):
    from sqlalchemy import func, select

    import app.services.session_storage as storage_module
    from app.models.auth_session import AuthSession

    service = IdentitySessionService(
        storage=SessionStorageService(tmp_path / "state"), verifier=lambda *args: True
    )
    profile_id = seeded_records["credential"].id
    config = ManualLoginRequest(
        profile_id=profile_id,
        login_url="http://localhost:8080/login",
        validate_url="http://localhost:8080/me",
        success_text="signed in",
    )

    def fail(_):
        raise OSError("private path must not escape")

    monkeypatch.setattr(storage_module.os, "fsync", fail)
    with session_scope() as session:
        count = session.scalar(select(func.count()).select_from(AuthSession))
        with pytest.raises(InputValidationError, match="保存登录状态失败"):
            service.import_state(session, profile_id, '{"cookies":[],"origins":[]}', config)
        assert session.scalar(select(func.count()).select_from(AuthSession)) == count
    assert not list((tmp_path / "state").glob("*.json"))
