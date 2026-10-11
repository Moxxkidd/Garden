"""Shared public project representations; private fingerprints stay internal."""

from app.models.target import Target
from app.services.asset_catalog import safe_url


def entry_view(session, entry):
    target = session.get(Target, entry.target_id)
    binding_status = "valid"
    if not entry.enabled:
        binding_status = "disabled"
    elif not target or target.status != "active" or target.base_url != entry.target_base_url:
        binding_status = "needs_rebind"
    return dict(
        id=entry.id,
        project_id=entry.project_id,
        target_id=entry.target_id,
        name=entry.name,
        entry_url=safe_url(entry.entry_url),
        origin=entry.origin,
        enabled=entry.enabled,
        binding_status=binding_status,
    )


def run_view(run):
    return dict(
        id=run.id,
        project_id=run.project_id,
        entry_id=run.entry_id,
        source=run.source,
        run_id=run.run_id,
        status=run.status,
        error_code=run.error_code,
        observation_count=run.observation_count,
        candidate_count=run.candidate_count,
        scope=run.snapshot.get("scope"),
    )


def correction_view(correction):
    return dict(
        id=correction.id,
        project_id=correction.project_id,
        action=correction.action,
        status=correction.status,
        target_asset_id=correction.target_asset_id,
        source_asset_ids=correction.source_asset_ids,
        reason=correction.reason,
        revision=correction.revision,
    )
