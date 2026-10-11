"""Index only explicitly recorded discovery links; unresolved endpoints stay unresolved."""

from collections import defaultdict

from sqlalchemy import select

from app.core.errors import ResourceNotFoundError
from app.models.project import ProjectRun
from app.models.project_asset import ProjectCandidate, ProjectObservation
from app.models.project_curation import ProjectRelation
from app.services.project_source import digest


class ProjectRelationService:
    def index(self, session, project_run_id):
        run = session.get(ProjectRun, project_run_id)
        if not run:
            raise ResourceNotFoundError("project_run_missing")
        observations = list(
            session.scalars(
                select(ProjectObservation).where(ProjectObservation.project_run_id == run.id)
            )
        )
        candidates = list(
            session.scalars(
                select(ProjectCandidate).where(ProjectCandidate.project_run_id == run.id)
            )
        )
        by_url = defaultdict(set)
        for row in observations:
            by_url[row.record["url"]].add(row.asset_id)
        existing = set(
            session.scalars(
                select(ProjectRelation.edge_key).where(ProjectRelation.project_run_id == run.id)
            )
        )
        for row in observations + candidates:
            candidate = isinstance(row, ProjectCandidate)
            for source in (row.record.get("discovery") or {}).get("sources", []):
                kind = source.get("kind")
                if kind == "js_literal":
                    relation_kind = "script_hint"
                elif kind in {
                    "html_link",
                    "iframe",
                    "resource",
                    "form_action",
                    "browser_navigation",
                    "browser_request",
                }:
                    relation_kind = "page_reference"
                else:
                    continue
                url = source.get("url")
                if not url:
                    continue
                from_ids = by_url[url]
                from_id = next(iter(from_ids)) if len(from_ids) == 1 else None
                detail = dict(
                    source_key=row.source_key,
                    source_kind=kind,
                    from_url=url,
                    to_url=row.record["url"],
                    source_index=source.get("index"),
                    from_state="observed" if from_id else "unresolved",
                    to_state="candidate" if candidate else "observed",
                    evidence_ids=row.record.get("evidence_ids"),
                    sources_truncated=(row.record.get("discovery") or {}).get("truncated", False),
                )
                key = digest([relation_kind, detail])
                if key in existing:
                    continue
                session.add(
                    ProjectRelation(
                        project_id=run.project_id,
                        project_run_id=run.id,
                        edge_key=key,
                        kind=relation_kind,
                        from_asset_id=from_id,
                        to_asset_id=row.asset_id,
                        detail=detail,
                    )
                )
                existing.add(key)
        for relation in run.snapshot.get("relations", []):
            from_ids, to_ids = by_url[relation["from_url"]], by_url[relation["to_url"]]
            from_id = next(iter(from_ids)) if len(from_ids) == 1 else None
            to_id = next(iter(to_ids)) if len(to_ids) == 1 else None
            detail = {
                **relation,
                "from_state": "observed" if from_id else "unresolved",
                "to_state": "observed" if to_id else "unresolved",
            }
            key = digest([relation["kind"], detail])
            if key not in existing:
                session.add(
                    ProjectRelation(
                        project_id=run.project_id,
                        project_run_id=run.id,
                        edge_key=key,
                        kind=relation["kind"],
                        from_asset_id=from_id,
                        to_asset_id=to_id,
                        detail=detail,
                    )
                )
                existing.add(key)
        session.flush()
        return len(existing)

    def list(self, session, project_id, query):
        from app.services.project_catalog import ProjectCatalogService

        return ProjectCatalogService().list(
            session, project_id, query.model_copy(update={"view": "relations"})
        )
