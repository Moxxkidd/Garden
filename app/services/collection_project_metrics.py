"""Freeze project indexing outcomes once, at durable scan finalization."""

from pathlib import Path

from sqlalchemy import select, update

from app.db.bootstrap import session_scope
from app.models.collection_queue import CollectionQueue
from app.models.project import ProjectRun
from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES, ScanRun
from app.services.collection_transactions import atomic_collection


def finalize_project_metrics(run_id):
    with session_scope() as session, atomic_collection(session):
        run = session.get(ScanRun, run_id)
        if run is None or run.status not in TERMINAL_SCAN_RUN_STATUSES:
            return
        queues = list(
            session.scalars(select(CollectionQueue).where(CollectionQueue.run_id == run_id))
        )
        if not any(
            q.telemetry is not None and "project_ingestion" not in q.telemetry for q in queues
        ):
            return
        # Serialize finalizers before reading the frozen marker. The write does
        # not reopen a queue or alter its lease, work, or source scan result.
        session.execute(
            update(CollectionQueue)
            .where(CollectionQueue.run_id == run_id)
            .values(revision=CollectionQueue.revision + 1)
        )
        memberships = list(
            session.scalars(
                select(ProjectRun)
                .where(ProjectRun.source == "scan", ProjectRun.run_id == run_id)
                .order_by(ProjectRun.project_id)
            )
        )
        for queue in queues:
            session.refresh(queue)
            if queue.telemetry is None or "project_ingestion" in queue.telemetry:
                continue
            projects = []
            for membership in memberships:
                metrics = (membership.snapshot or {}).get("ingestion_metrics")
                measured = membership.status == "complete" and metrics is not None
                counts = (metrics or {}).get("contexts", {}).get(str(queue.context_id), {})
                projects.append(
                    {
                        "project_id": membership.project_id,
                        "status": membership.status,
                        "new_subjects": counts.get("new_subjects", 0) if measured else None,
                        "new_observations": counts.get("new_observations", 0) if measured else None,
                    }
                )
            state = (
                "not_applicable"
                if not projects
                else (
                    "complete"
                    if all(p["new_subjects"] is not None for p in projects)
                    else "unknown"
                )
            )
            queue.telemetry = {
                **queue.telemetry,
                "project_ingestion": {"state": state, "projects": projects},
            }
        session.flush()
        if run.report_path:
            from app.services.scan_reporting import ScanReportService

            ScanReportService(Path(run.report_path).parent).generate(session, run_id)
