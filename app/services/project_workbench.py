"""SQL workbench projections; saved queries and grouping never rewrite asset facts."""

import json

from sqlalchemy import func, select

from app.core.errors import ConflictError, InputValidationError, ResourceNotFoundError
from app.models.auth_session import AuthSession
from app.models.credential_profile import CredentialProfile
from app.models.project import ProjectEntry, ProjectRun
from app.models.project_asset import ProjectAsset, ProjectObservation
from app.models.project_curation import ProjectAnnotation, ProjectSavedView
from app.schemas.projects import ProjectPage
from app.services.project_catalog import ProjectCatalogService
from app.services.project_transactions import atomic_import
from app.services.projects import ProjectService


class ProjectWorkbenchService:
    def templates(self, session, project_id, query):
        if query.view != "assets":
            raise InputValidationError("路径模板仅用于资产主体，请选择资产主体视图。")
        project = ProjectService().get(session, project_id)
        catalog = ProjectCatalogService()
        filtered = catalog._observations(project_id, query).subquery()
        key = func.garden_path_family(ProjectAsset.url, ProjectAsset.method)
        groups = (
            select(
                key.label("key"),
                func.count(func.distinct(filtered.c.display_asset_id)).label("instance_count"),
                func.count().label("observation_count"),
                func.max(filtered.c.seen_at).label("last_seen"),
                func.json_group_array(
                    func.distinct(
                        func.json_object(
                            "identity",
                            filtered.c.identity_key,
                            "codes",
                            filtered.c.record["status_codes"].as_json(),
                        )
                    )
                ).label("identities"),
            )
            .join(ProjectAsset, ProjectAsset.id == filtered.c.display_asset_id)
            .group_by(key)
            .subquery()
        )
        matched = session.scalar(select(func.count()).select_from(groups))
        order = groups.c.last_seen if query.sort == "last_seen" else groups.c.key
        selected = session.execute(
            select(groups)
            .order_by(order.desc() if query.order == "desc" else order.asc(), groups.c.key)
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        ).mappings()
        items = []
        for row in selected:
            origin, method, path, parameters, route = json.loads(row["key"])
            identities = {}
            for observed in json.loads(row["identities"]):
                identities.setdefault(observed["identity"], set()).update(observed["codes"])
            items.append(
                dict(
                    key=row["key"],
                    site=origin,
                    method=method,
                    path=path,
                    parameters=parameters,
                    route=route,
                    instance_count=row["instance_count"],
                    observation_count=row["observation_count"],
                    last_seen=row["last_seen"].isoformat() if row["last_seen"] else None,
                    status_codes=sorted({c for codes in identities.values() for c in codes}),
                    identities={k: sorted(v) for k, v in identities.items()},
                )
            )
        return ProjectPage(
            items=items,
            matched=matched,
            counts=catalog._counts(session, project_id),
            revision=project.revision,
            limitations=["模板仅聚合展示，保留原始资产、方法、站点和身份；不证明业务有效。"],
        )

    def views(self, session, project_id):
        ProjectService().get(session, project_id)
        return [
            dict(id=r.id, name=r.name, query=r.query)
            for r in session.scalars(
                select(ProjectSavedView)
                .where(ProjectSavedView.project_id == project_id)
                .order_by(ProjectSavedView.id)
            )
        ]

    def save_view(self, session, project_id, request):
        with atomic_import(session):
            if not request.name.strip():
                raise InputValidationError("invalid_saved_view_name")
            existing = session.scalar(
                select(ProjectSavedView.id).where(
                    ProjectSavedView.project_id == project_id,
                    ProjectSavedView.name == request.name.strip(),
                )
            )
            if existing is not None:
                raise ConflictError("saved_view_name_exists")
            ProjectService().bump(session, project_id, request.expected_revision)
            row = ProjectSavedView(
                project_id=project_id,
                name=request.name.strip(),
                query=request.query.model_copy(update={"page": 1}).model_dump(
                    mode="json", exclude_none=True
                ),
            )
            session.add(row)
            session.flush()
            return row

    def delete_view(self, session, project_id, view_id, expected_revision):
        with atomic_import(session):
            row = session.get(ProjectSavedView, view_id)
            if row is None or row.project_id != project_id:
                raise ResourceNotFoundError("project_saved_view_missing")
            ProjectService().bump(session, project_id, expected_revision)
            session.delete(row)
            session.flush()

    def summary(self, session, project_id):
        catalog = ProjectCatalogService()
        ProjectService().get(session, project_id)
        categories = dict(
            session.execute(
                select(ProjectAsset.kind, func.count(func.distinct(ProjectAsset.id)))
                .join(ProjectObservation, ProjectObservation.display_asset_id == ProjectAsset.id)
                .where(ProjectAsset.project_id == project_id)
                .group_by(ProjectAsset.kind)
            ).all()
        )
        latest_session = (
            select(AuthSession.id)
            .where(AuthSession.credential_profile_id == CredentialProfile.id)
            .order_by(AuthSession.id.desc())
            .limit(1)
            .correlate(CredentialProfile)
            .scalar_subquery()
        )
        identities = [
            dict(
                name=name,
                role=role,
                status="已撤销" if revoked else status or "尚无保存会话",
                validated=validated.isoformat() if validated else "未记录验证时间",
            )
            for name, role, status, revoked, validated in session.execute(
                select(
                    CredentialProfile.name,
                    CredentialProfile.role,
                    AuthSession.status,
                    AuthSession.revoked_at,
                    AuthSession.last_validated_at,
                )
                .outerjoin(AuthSession, AuthSession.id == latest_session)
                .where(
                    CredentialProfile.target_id.in_(
                        select(ProjectEntry.target_id).where(ProjectEntry.project_id == project_id)
                    )
                )
            )
        ]
        recent = list(
            session.scalars(
                select(ProjectRun)
                .where(ProjectRun.project_id == project_id)
                .order_by(ProjectRun.id.desc())
                .limit(5)
            )
        )
        scope = ProjectRun.snapshot["scope"]["completeness"].as_string()
        gaps = session.scalar(
            select(func.count())
            .select_from(ProjectRun)
            .where(
                ProjectRun.project_id == project_id,
                (scope.is_(None) | (scope != "complete") | (ProjectRun.status != "complete")),
            )
        )
        pending = session.scalar(
            select(func.count())
            .select_from(ProjectAnnotation)
            .join(ProjectAsset, ProjectAsset.id == ProjectAnnotation.asset_id)
            .where(ProjectAsset.project_id == project_id, ProjectAnnotation.needs_review.is_(True))
        )
        return dict(
            counts=catalog._counts(session, project_id),
            categories=categories,
            identities=identities,
            recent=recent,
            gaps=gaps,
            pending=pending,
        )
