import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanRun


def test_preview_is_read_only_and_stale_profile_refuses_start(app, seeded_records):
    with TestClient(app) as client:
        body = {
            "url": seeded_records["target"].base_url,
            "target_id": seeded_records["target"].id,
            "profile_ids": [seeded_records["credential"].id],
        }
        response = client.post("/api/identity-runs/preview", json=body)
        assert response.status_code == 200, response.text
        token = response.json()["preview_token"]
        with session_scope() as session:
            assert session.scalar(select(func.count()).select_from(ScanRun)) == 0
            from app.models.credential_profile import CredentialProfile

            session.get(CredentialProfile, seeded_records["credential"].id).username = "changed"
        result = client.post("/api/identity-runs", json={"preview_token": token})
        assert result.status_code == 400


def test_import_invalid_payload_never_echoes_state(app, seeded_records):
    with TestClient(app) as client:
        private = "PRIVATE_SESSION_VALUE"
        response = client.post("/api/identity-sessions/import", json={"raw_json": private})
        assert response.status_code == 400
        assert private not in response.text
        response = client.post("/api/identity-sessions/import", content=private * 200000)
        assert response.status_code == 400
        assert private not in response.text
        assert (
            client.post(
                "/api/identity-runs/preview", json={}, headers={"Origin": "https://outside.test"}
            ).status_code
            == 400
        )
        page = client.get("/identities")
        assert page.status_code == 200
        assert "Garden 所在电脑" in page.text
        assert 'method="post"' in page.text


def test_native_form_preview_keeps_query_and_state_private(app, seeded_records):
    with TestClient(app) as client:
        response = client.post(
            "/identities/preview",
            data={
                "target_id": seeded_records["target"].id,
                "url": seeded_records["target"].base_url + "?token=PRIVATE",
                "profile_ids": str(seeded_records["credential"].id),
                "max_pages": "10",
            },
        )
        assert response.status_code == 200, response.text
        assert "PRIVATE" not in response.text
        assert "确认开始采集" in response.text
        assert "preview_token" in response.text


def test_api_import_uses_fresh_browser_and_does_not_start_scan(app, seeded_records):
    from app.models.target import Target
    from tests.fixtures.identity_site import identity_site
    from tests.test_scan_preview_e2e import _serve

    fixture, state = identity_site()
    state["sessions"]["token"] = "reader"
    with _serve(fixture) as base:
        with session_scope() as session:
            session.get(Target, seeded_records["target"].id).base_url = base
        with TestClient(app) as client:
            profile_id = seeded_records["credential"].id
            response = client.post(
                "/api/identity-sessions/import",
                json={
                    "profile_id": profile_id,
                    "raw_json": json.dumps(
                        {
                            "cookies": [
                                {
                                    "name": "identity",
                                    "value": "token",
                                    "domain": "127.0.0.1",
                                    "path": "/",
                                }
                            ],
                            "origins": [],
                        }
                    ),
                    "verification": {
                        "profile_id": profile_id,
                        "login_url": base + "/login",
                        "validate_url": base + "/me",
                        "success_selector": "#identity",
                    },
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "ready"
            with session_scope() as session:
                assert session.scalar(select(func.count()).select_from(ScanRun)) == 0


def test_automatic_http_profile_converts_with_positive_restore(app, seeded_records):
    from app.models.credential_profile import CredentialProfile
    from app.models.target import Target
    from app.services.login_configs import encode_inline_login_config
    from tests.fixtures.identity_site import identity_site
    from tests.test_scan_preview_e2e import _serve

    fixture, _ = identity_site()
    with _serve(fixture) as base:
        with session_scope() as session:
            session.get(Target, seeded_records["target"].id).base_url = base
            profile = session.get(CredentialProfile, seeded_records["credential"].id)
            profile.username = "reader"
            profile.secret_ref = "literal://123456"
            profile.login_config_path = encode_inline_login_config(
                {
                    "adapter": "http",
                    "retry_attempts": 1,
                    "login_request": {
                        "url": "/login",
                        "body_type": "form",
                        "body": {"username": "reader", "code": "123456"},
                    },
                    "validate_request": {
                        "method": "GET",
                        "url": "/me",
                        "success_contains": "reader",
                    },
                }
            )
        with TestClient(app) as client:
            response = client.post(f"/api/identity-profiles/{profile.id}/activate")
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "ready"
            assert "123456" not in response.text


def test_cli_import_limits_and_identity_mode_boundary(tmp_path):
    import pytest
    from pydantic import ValidationError
    from typer.testing import CliRunner

    from app.cli.main import app as cli
    from app.schemas.assessment import AssessmentStartRequest

    path = tmp_path / "private-state.json"
    path.write_text("PRIVATE" * 150000)
    result = CliRunner().invoke(
        cli,
        [
            "identities",
            "import",
            str(path),
            "--profile-id",
            "1",
            "--login-url",
            "https://example.test/login",
            "--validate-url",
            "https://example.test/me",
            "--success-selector",
            "#me",
        ],
    )
    assert result.exit_code != 0
    assert "PRIVATE" not in result.output
    assert "1 MiB" in result.output
    with pytest.raises(ValidationError):
        AssessmentStartRequest(url="https://example.test", mode="identity_collection")
