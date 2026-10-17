"""Task membership and separately retryable project indexing."""

import logging

from sqlalchemy import select

from app.core.errors import ConflictError, ResourceNotFoundError
from app.db.bootstrap import session_scope
from app.models.project import ProjectEntry, ProjectRun
from app.services.project_ingestion import ProjectIngestionService
from app.services.project_source import ProjectSourceService, digest
from app.services.project_transactions import atomic_import
from app.services.projects import ProjectService, project_url

logger = logging.getLogger(__name__)


class ProjectLifecycleService:
    def __init__(self):
        self.sources = ProjectSourceService()
        self.projects = ProjectService()
        self.ingestion = ProjectIngestionService()

    def validate(self, session, entry_id, target_id, url):
        entry = session.get(ProjectEntry, entry_id)
        if entry is None:
            raise ResourceNotFoundError("project_entry_not_found")
        self.projects.validate_entry(session, entry.project_id, entry.id)
        if entry.target_id != target_id or (
            url is not None and entry.entry_url != project_url(url)
        ):
            raise ConflictError("source_entry_mismatch")
        return entry

    def attach(self, session, entry_id, ref):
        run = self.sources.source(session, ref)
        entry = self.validate(
            session,
            entry_id,
            run.target_id,
            None,
        )
        if self.sources.entry_digest(ref, run) != digest(project_url(entry.entry_url)):
            raise ConflictError("source_entry_mismatch")
        existing = session.scalar(
            select(ProjectRun).where(
                ProjectRun.project_id == entry.project_id,
                ProjectRun.source == ref.source,
                ProjectRun.run_id == ref.run_id,
            )
        )
        if existing:
            if existing.entry_id != entry_id:
                raise ConflictError("source_already_bound_to_entry")
            return existing
        with atomic_import(session):
            self.projects.bump(session, entry.project_id)
            membership = ProjectRun(
                project_id=entry.project_id, entry_id=entry_id, source=ref.source, run_id=ref.run_id
            )
            session.add(membership)
            session.flush()
        return membership

    def retry(self, project_run_id):
        with session_scope() as session:
            return self.ingestion.sync(session, project_run_id)

    def reconcile(self, ref):
        # Index failures must never change the task result or expose source data in logs.
        try:
            with session_scope() as session:
                source = self.sources.source(session, ref)
                if not self.sources.terminal(ref, source):
                    return
                ids = list(
                    session.scalars(
                        select(ProjectRun.id).where(
                            ProjectRun.source == ref.source,
                            ProjectRun.run_id == ref.run_id,
                            ProjectRun.status != "complete",
                        )
                    )
                )
            for project_run_id in ids:
                try:
                    self.retry(project_run_id)
                except Exception:
                    with session_scope() as session:
                        membership = session.get(ProjectRun, project_run_id)
                        if membership and membership.status != "complete":
                            membership.status = "failed"
                            membership.error_code = "project_index_failed"
        except Exception:
            logger.warning("Project indexing unavailable; explicit sync may be required.")
        finally:
            if ref.source == "scan":
                from app.services.collection_project_metrics import finalize_project_metrics

                try:
                    finalize_project_metrics(ref.run_id)
                except Exception:
                    logger.warning("Collection project metrics finalization unavailable.")
