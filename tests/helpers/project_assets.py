from datetime import datetime, timezone

from app.models.scan_context import ScanContext
from app.models.scan_run import ScanAsset, ScanRun
from app.schemas.projects import ProjectEntryCreate, ProjectImportRequest, ProjectSourceRef
from app.services.projects import ProjectService


def project_fixture(session, target):
    target.base_url = "http://localhost:8111/"
    service = ProjectService()
    p = service.create(session, "Demo", "")
    entry = service.add_entry(
        session,
        p.id,
        ProjectEntryCreate(target_id=target.id, entry_url=target.base_url, name="Main"),
    )
    return p, entry


def source_run(
    session,
    target,
    *,
    status_code=200,
    day=1,
    completeness="complete",
    path="/asset",
    profile_id=None,
):
    now = datetime(2026, 10, day, tzinfo=timezone.utc)
    run = ScanRun(
        input_url=target.base_url,
        normalized_url=target.base_url,
        target_id=target.id,
        mode="quick",
        status="completed",
        completeness=completeness,
        finished_at=now,
        options={},
    )
    session.add(run)
    session.flush()
    context = ScanContext(
        scan_run_id=run.id,
        kind="anonymous" if profile_id is None else "identity",
        context_key="anonymous" if profile_id is None else f"profile:{profile_id}",
        credential_profile_id=profile_id,
        status="completed",
        collection_status="completed",
        completeness=completeness,
        health_status="ready",
    )
    session.add(context)
    session.flush()
    asset = ScanAsset(
        scan_run_id=run.id,
        context_id=context.id,
        asset_type="page",
        method="GET",
        url=target.base_url.rstrip("/") + path,
        status_code=status_code,
        discovered_at=now,
        attributes={"identity_assessment": "confirmed"},
    )
    session.add(asset)
    session.flush()
    return run


def import_request(entry, run):
    return ProjectImportRequest(
        entry_id=entry.id, source_ref=ProjectSourceRef(source="scan", run_id=run.id)
    )
