from pathlib import Path

import pytest

from app.core.errors import InputValidationError
from app.db.bootstrap import session_scope
from app.models.credential_profile import CredentialProfile
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanRun
from app.schemas.identity import RecoveryRequest
from app.services.identity_recovery import IdentityRecoveryService
from app.services.session_storage import SessionStorageService


def checkpoint_fixture(seeded_records, tmp_path):
    storage = SessionStorageService(tmp_path / "private")
    service = IdentityRecoveryService(storage=storage)
    with session_scope() as session:
        target = seeded_records["target"]
        profile = seeded_records["credential"]
        run = ScanRun(
            target_id=target.id,
            mode="identity_collection",
            input_url=target.base_url,
            normalized_url=target.base_url,
            status="completed_with_warnings",
            completeness="incomplete",
            options={},
        )
        session.add(run)
        session.flush()
        context = ScanContext(
            scan_run_id=run.id,
            kind="identity",
            context_key=f"profile:{profile.id}",
            credential_profile_id=profile.id,
            completeness="incomplete",
        )
        session.add(context)
        session.flush()
        version = service.save(
            session,
            context.id,
            [{"url": target.base_url + "/pending?key=private", "method": "GET"}],
            [{"url": target.base_url + "/uncertain", "method": "GET"}],
        )
        request = RecoveryRequest(
            source_run_id=run.id, source_context_id=context.id, checkpoint_version=version
        )
    return service, request


def test_checkpoint_is_private_and_preview_read_only(seeded_records, tmp_path):
    service, request = checkpoint_fixture(seeded_records, tmp_path)
    with session_scope() as session:
        preview = service.preview(session, request)
        assert preview.pending_count == 1
        assert preview.uncertain_count == 1
        assert "private" not in preview.model_dump_json()
        assert preview.preview_token
        assert not session.new and not session.dirty
        checkpoint = service._load(session, request)[2]
        assert Path(checkpoint.storage_ref).stat().st_mode & 0o777 == 0o600
        assert "private" in Path(checkpoint.storage_ref).read_text()


def test_recovery_rejects_stale_missing_or_edited_profile(seeded_records, tmp_path):
    service, request = checkpoint_fixture(seeded_records, tmp_path)
    with session_scope() as session:
        preview = service.preview(session, request)
        profile = session.get(CredentialProfile, seeded_records["credential"].id)
        profile.username = "different-account"
        session.flush()
        with pytest.raises(InputValidationError):
            service.start(session, request, preview.preview_token)
        with pytest.raises(InputValidationError):
            service.preview(session, request.model_copy(update={"checkpoint_version": 99}))
        checkpoint = service._load(session, request)[2]
        Path(checkpoint.storage_ref).unlink()
        with pytest.raises(InputValidationError):
            service.preview(session, request)


def test_checkpoint_rejects_cross_origin_and_mutating_work(seeded_records, tmp_path):
    service, request = checkpoint_fixture(seeded_records, tmp_path)
    with session_scope() as session:
        with pytest.raises(InputValidationError):
            service.save(
                session,
                request.source_context_id,
                [{"url": "https://outside.invalid/private", "method": "GET"}],
                [],
            )
        with pytest.raises(InputValidationError):
            service.save(
                session,
                request.source_context_id,
                [{"url": seeded_records["target"].base_url, "method": "POST"}],
                [],
            )


def test_recovery_transport_preserves_head_and_known_scope():
    from app.core.settings import Settings
    from app.schemas.scan import ScanOptions
    from app.services.browser_discovery import BrowserDiscovery
    from app.services.scan_network import TargetNetworkPolicy
    from tests.test_discovery_browser import site

    observations = []
    with site() as (base, requests):
        work = [{"url": base + "/", "method": "GET"}, {"url": base + "/head", "method": "HEAD"}]
        BrowserDiscovery(TargetNetworkPolicy(Settings(allow_private_targets=True))).collect(
            base,
            ScanOptions(render_wait_ms=100),
            lambda: None,
            lambda result, route: observations.append(result),
            lambda item: None,
            allowed_urls={item["url"] for item in work},
            recovery_work=work,
        )
    assert ("GET", "/delayed") not in requests
    assert ("GET", "/head") not in requests
    assert any(r.method == "HEAD" and r.final_url.endswith("/head") for r in observations)
