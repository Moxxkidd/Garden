"""Atomic, idempotent indexing of terminal tasks into a project."""

from sqlalchemy import select, update

from app.core.errors import ConflictError, ResourceNotFoundError
from app.models.project import ProjectRun
from app.models.project_asset import ProjectAsset, ProjectCandidate, ProjectObservation
from app.schemas.projects import ProjectImportPreview, ProjectSourceRef
from app.services.project_identity import project_asset_key, project_identity_key
from app.services.project_source import ProjectSourceService
from app.services.project_tokens import ProjectTokens
from app.services.project_transactions import atomic_import
from app.services.projects import ProjectService


class ProjectIngestionService:
    def __init__(self, tokens=None):
        self.tokens = tokens or ProjectTokens()
        self.projects = ProjectService()
        self.sources = ProjectSourceService()

    def runs(self, session, project_id):
        self.projects.get(session, project_id)
        return list(
            session.scalars(
                select(ProjectRun)
                .where(ProjectRun.project_id == project_id)
                .order_by(ProjectRun.id)
            )
        )

    def preview(self, session, project_id, request):
        snapshot = self.sources.snapshot(session, request.source_ref)
        self.sources.validate_entry(session, project_id, request.entry_id, snapshot)
        project = self.projects.get(session, project_id)
        token = self.tokens.put(
            (project_id, request.model_copy(deep=True), snapshot.digest, project.revision)
        )
        return ProjectImportPreview(
            token=token,
            subject_count=len({project_asset_key(r) for r in snapshot.records}),
            observation_count=len(snapshot.records),
            candidate_count=len(snapshot.candidates),
            limitations=[
                snapshot.scope.coverage_note,
                "资产主体为路由族，不代表业务有效或持续在线。",
            ],
        )

    def confirm(self, session, project_id, preview_token):
        with atomic_import(session):
            return self._confirm(session, project_id, preview_token)

    def _confirm(self, session, project_id, preview_token):
        owner, request, expected_digest, revision = self.tokens.get(preview_token)
        if owner != project_id:
            raise ConflictError("project_preview_mismatch")
        snapshot = self.sources.snapshot(session, request.source_ref)
        self.sources.validate_entry(session, project_id, request.entry_id, snapshot)
        if snapshot.digest != expected_digest:
            raise ConflictError("source_changed")
        existing = session.scalar(
            select(ProjectRun).where(
                ProjectRun.project_id == project_id,
                ProjectRun.source == request.source_ref.source,
                ProjectRun.run_id == request.source_ref.run_id,
            )
        )
        if existing and existing.entry_id != request.entry_id:
            raise ConflictError("source_already_bound_to_entry")
        if existing and existing.status == "complete":
            if existing.source_digest != snapshot.digest:
                raise ConflictError("source_changed")
            return existing
        self.projects.bump(session, project_id, revision)
        if existing is None:
            existing = ProjectRun(
                project_id=project_id,
                entry_id=request.entry_id,
                source=request.source_ref.source,
                run_id=request.source_ref.run_id,
            )
            session.add(existing)
            session.flush()
        if existing.entry_id != request.entry_id:
            raise ConflictError("source_already_bound_to_entry")
        return self._index(session, existing, snapshot)

    def sync(self, session, project_run_id):
        with atomic_import(session):
            return self._sync(session, project_run_id)

    def _sync(self, session, project_run_id):
        run = session.get(ProjectRun, project_run_id)
        if run is None:
            raise ResourceNotFoundError("project_run_missing")
        snapshot = self.sources.snapshot(
            session, ProjectSourceRef(source=run.source, run_id=run.run_id)
        )
        self.sources.validate_entry(session, run.project_id, run.entry_id, snapshot)
        if run.status == "complete":
            if run.source_digest != snapshot.digest:
                raise ConflictError("source_changed")
            return run
        self.projects.bump(session, run.project_id)
        return self._index(session, run, snapshot)

    def _index(self, session, run, snapshot):
        run.status = "importing"
        metrics = {"version": 1, "new_subjects": 0, "new_observations": 0, "contexts": {}}
        assets = {
            a.natural_key: a
            for a in session.scalars(
                select(ProjectAsset).where(
                    ProjectAsset.project_id == run.project_id, ProjectAsset.manual.is_(False)
                )
            )
        }
        for row in snapshot.records:
            context_metrics = metrics["contexts"].setdefault(
                str(row.context_id), {"new_subjects": 0, "new_observations": 0}
            )
            key = project_asset_key(row)
            asset = assets.get(key)
            if asset is None:
                asset = ProjectAsset(
                    project_id=run.project_id,
                    natural_key=key,
                    site=row.site,
                    method=row.method,
                    kind=row.kind,
                    original_type=row.original_type,
                    url=row.route_url or row.url,
                )
                session.add(asset)
                session.flush()
                assets[key] = asset
                metrics["new_subjects"] += 1
                context_metrics["new_subjects"] += 1
            metrics["new_observations"] += 1
            context_metrics["new_observations"] += 1
            session.add(
                ProjectObservation(
                    project_id=run.project_id,
                    project_run_id=run.id,
                    asset_id=asset.id,
                    display_asset_id=asset.id,
                    source_key=row.asset_id,
                    identity_key=project_identity_key(snapshot.scope, row),
                    seen_at=row.last_seen,
                    first_seen=row.first_seen,
                    record=row.model_dump(mode="json"),
                    search_text=" ".join(
                        [row.url, row.title or "", row.display_name or "", row.context]
                    ).casefold(),
                )
            )
        for row in snapshot.candidates:
            key = project_asset_key(row)
            session.add(
                ProjectCandidate(
                    project_id=run.project_id,
                    project_run_id=run.id,
                    source_key=row.asset_id,
                    natural_key=key,
                    identity_key=project_identity_key(snapshot.scope, row),
                    asset_id=assets[key].id if key in assets else None,
                    record=row.model_dump(mode="json"),
                )
            )
        session.flush()
        for key, asset in assets.items():
            session.execute(
                update(ProjectCandidate)
                .where(
                    ProjectCandidate.project_id == run.project_id,
                    ProjectCandidate.natural_key == key,
                    ProjectCandidate.asset_id.is_(None),
                )
                .values(asset_id=asset.id)
            )
        # Freeze the existing display-safe request grouping with the source snapshot.
        # No protected request files or fingerprints are exported.
        from app.schemas.assets import AssetQuery
        from app.services.asset_catalog import AssetCatalogService

        groups = (
            AssetCatalogService()
            .select(session, AssetQuery(source=run.source, run_id=run.run_id, view="grouped"))
            .items
        )
        request_variants = {}
        for group in groups:
            for row in group.observations:
                request_variants[row.asset_id] = [
                    variant.model_dump(mode="json")
                    for variant in group.variants
                    if row.asset_id in variant.observation_ids
                ]
        run.snapshot = {
            "scope": snapshot.scope.model_dump(mode="json"),
            "comparison_metadata": snapshot.comparison_metadata,
            "relations": snapshot.relations,
            "ingestion_metrics": metrics,
            "request_variants": request_variants,
        }
        from app.services.project_relations import ProjectRelationService

        ProjectRelationService().index(session, run.id)
        from app.services.project_curation import ProjectCurationService

        ProjectCurationService().project_new_observations(session, run.id)
        run.source_digest = snapshot.digest
        run.observation_count = len(snapshot.records)
        run.candidate_count = len(snapshot.candidates)
        run.status = "complete"
        run.error_code = None
        session.flush()
        return run
