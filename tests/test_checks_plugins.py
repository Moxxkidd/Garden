from app.db.bootstrap import session_scope
from app.security_checks.administrative_surface import AdministrativeSurfaceCheck
from app.security_checks.auth_headers_cache import AuthenticatedHeadersCacheCheck
from app.security_checks.base import CheckContext
from app.security_checks.debug_leakage import DebugLeakageCheck
from app.security_checks.idor_indicators import IdorIndicatorsCheck
from app.security_checks.sensitive_disclosure import SensitiveDisclosureCheck
from app.security_checks.upload_import import UploadImportCheck
from app.services.credentials import CredentialProfileService
from app.services.inventory import InventoryBuildService
from app.services.jobs import ScanJobService
from app.services.sessions import AuthSessionService
from app.services.targets import TargetService


def _build_context(inventory_run_id: int) -> CheckContext:
    inventory_service = InventoryBuildService()
    with session_scope() as session:
        inventory_run = inventory_service.get(session, inventory_run_id)
        target = TargetService().get(session, inventory_run.target_id)
        credential = CredentialProfileService().get(session, inventory_run.credential_profile_id)
        auth_session = AuthSessionService().get(session, inventory_run.auth_session_id)
        job = ScanJobService().get(session, inventory_run.scan_job_id)
    return CheckContext(
        inventory_run=inventory_run,
        target=target,
        credential_profile=credential,
        auth_session=auth_session,
        inventory_job=job,
        pages=list(inventory_run.pages),
        endpoints=list(inventory_run.endpoints),
        parameters=list(inventory_run.parameters),
        annotations=list(inventory_run.annotations),
    )


def test_debug_leakage_check_triggers(seeded_inventory) -> None:
    results = DebugLeakageCheck().run(_build_context(seeded_inventory["inventory_run_id"]))
    assert len(results) == 1
    assert "Debug or diagnostic artifacts" in results[0].title


def test_authenticated_headers_cache_check_triggers(seeded_inventory) -> None:
    results = AuthenticatedHeadersCacheCheck().run(
        _build_context(seeded_inventory["inventory_run_id"])
    )
    assert len(results) == 1
    assert "cookie header posture" in results[0].title


def test_administrative_surface_check_triggers(seeded_inventory) -> None:
    results = AdministrativeSurfaceCheck().run(_build_context(seeded_inventory["inventory_run_id"]))
    assert len(results) == 1
    assert "Administrative or diagnostic surfaces" in results[0].title


def test_idor_indicator_check_triggers(seeded_inventory) -> None:
    results = IdorIndicatorsCheck().run(_build_context(seeded_inventory["inventory_run_id"]))
    assert len(results) == 1
    assert "IDOR review" in results[0].title


def test_upload_import_check_triggers(seeded_inventory) -> None:
    results = UploadImportCheck().run(_build_context(seeded_inventory["inventory_run_id"]))
    assert len(results) == 1
    assert "Upload or import entrypoints" in results[0].title


def test_sensitive_disclosure_check_triggers(seeded_inventory) -> None:
    results = SensitiveDisclosureCheck().run(_build_context(seeded_inventory["inventory_run_id"]))
    assert len(results) == 1
    assert "Sensitive implementation details" in results[0].title
