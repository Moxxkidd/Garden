"""Small display projection shared by streaming and status polling."""

from app.services.scan_result_presentation import (
    execution_summary,
    format_collection_activity,
    format_execution_progress,
    summarize_scan_view,
)


def progress_snapshot(scan):
    summary = summarize_scan_view(scan)
    return dict(
        id=scan.id,
        status=scan.status,
        current_stage=scan.current_stage,
        progress=scan.progress,
        progress_text=format_execution_progress(scan.progress),
        execution=execution_summary(scan.status),
        coverage=summary.coverage,
        findings=summary.findings,
        asset_count=scan.asset_count,
        evidence_count=scan.evidence_count,
        activity=format_collection_activity(scan.discovery_summary),
        waiting_reason=scan.waiting_reason,
        started_at=scan.started_at.isoformat() if scan.started_at else None,
        stages=[dict(name=s.name, status=s.status) for s in scan.stages],
    )
