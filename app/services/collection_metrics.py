"""One read-only projection of persisted observations and discovery counters."""

from sqlalchemy import func, select

from app.models.scan_context import ScanContext
from app.models.scan_run import ScanAsset, ScanEvidence, ScanRun


def observation_metrics(session, queue, requests_used):
    run = session.get(ScanRun, queue.run_id)
    context = session.get(ScanContext, queue.context_id)
    transport = (
        "browser"
        if run.mode == "identity_collection"
        else (run.options or {}).get("collection_mode", "http")
    )
    result = dict(
        transport=transport,
        confirmed_observations=None,
        uncertain_observations=None,
        diagnostic_observations=None,
        unknown_identity_observations=None,
        confirmed_observations_per_request=None,
        duplicate_candidates=None,
        project_ingestion=(queue.telemetry or {}).get("project_ingestion"),
    )
    if queue.telemetry is None:
        return result
    assessment = ScanEvidence.data["identity_assessment"].as_string()
    counts = dict(
        session.execute(
            select(assessment, func.count())
            .join(ScanAsset, ScanAsset.id == ScanEvidence.asset_id)
            .where(
                ScanAsset.context_id == queue.context_id,
                ScanEvidence.scan_run_id == queue.run_id,
                ScanEvidence.evidence_type == "http_response",
            )
            .group_by(assessment)
        ).all()
    )
    confirmed = sum(counts.values()) if context.kind == "anonymous" else counts.get("confirmed", 0)
    result.update(
        confirmed_observations=confirmed,
        uncertain_observations=counts.get("identity_uncertain", 0)
        if context.kind != "anonymous"
        else 0,
        diagnostic_observations=counts.get("auth_diagnostic", 0)
        if context.kind != "anonymous"
        else 0,
        unknown_identity_observations=sum(
            v
            for k, v in counts.items()
            if k not in {"confirmed", "identity_uncertain", "auth_diagnostic"}
        )
        if context.kind != "anonymous"
        else 0,
        confirmed_observations_per_request=confirmed / requests_used if requests_used else None,
    )
    discovery = (
        (context.identity_snapshot or {}).get("discovery")
        if run.mode == "identity_collection"
        else run.asset_metadata
    )
    if isinstance(discovery, dict) and discovery.get("version") == 2:
        result["duplicate_candidates"] = sum(
            row.get("duplicate_candidates", 0) for row in discovery.get("stats", {}).values()
        )
    return result
