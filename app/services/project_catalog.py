"""SQL-filtered project views over immutable observations, never live target state."""

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import aliased

from app.core.errors import ResourceNotFoundError
from app.models.inventory_run import InventoryRun
from app.models.project import ProjectRun
from app.models.project_asset import ProjectAsset, ProjectCandidate, ProjectObservation
from app.models.project_curation import ProjectAnnotation, ProjectCorrection, ProjectRelation
from app.models.scan_run import ScanRun
from app.schemas.projects import ProjectAssetDetail, ProjectPage, ProjectQuery
from app.services.project_source import canonical_value
from app.services.projects import ProjectService


class ProjectCatalogService:
    def _observations(self, project_id, query):
        o, a = ProjectObservation, ProjectAsset
        statement = select(o).join(a, a.id == o.display_asset_id).where(o.project_id == project_id)
        for value, column in [
            (query.site, a.site),
            (query.kind, a.kind),
            (query.method, a.method),
            (query.identity_key, o.identity_key),
            (query.project_run_id, o.project_run_id),
            (query.asset_id, o.display_asset_id),
            (query.original_asset_id, o.asset_id),
        ]:
            if value is not None:
                statement = statement.where(column == value)
        if query.q:
            statement = statement.where(o.search_text.contains(query.q.casefold(), autoescape=True))
        if query.seen_from is not None:
            statement = statement.where(o.seen_at >= query.seen_from)
        if query.seen_to is not None:
            statement = statement.where(o.seen_at <= query.seen_to)
        if query.tag:
            tags = func.json_each(ProjectAnnotation.tags).table_valued("value")
            statement = statement.where(
                select(ProjectAnnotation.asset_id)
                .where(
                    ProjectAnnotation.asset_id == a.id,
                    select(1).select_from(tags).where(tags.c.value == query.tag).exists(),
                )
                .exists()
            )
        return statement

    def _counts(self, session, project_id):
        return {
            "assets": session.scalar(
                select(func.count(func.distinct(ProjectObservation.display_asset_id))).where(
                    ProjectObservation.project_id == project_id
                )
            ),
            "observations": session.scalar(
                select(func.count())
                .select_from(ProjectObservation)
                .where(ProjectObservation.project_id == project_id)
            ),
            "candidates": session.scalar(
                select(func.count())
                .select_from(ProjectCandidate)
                .where(ProjectCandidate.project_id == project_id)
            ),
            "relations": session.scalar(
                select(func.count())
                .select_from(ProjectRelation)
                .where(ProjectRelation.project_id == project_id)
            ),
        }

    def _observation(self, row):
        return dict(
            id=row.id,
            asset_id=row.asset_id,
            display_asset_id=row.display_asset_id,
            project_run_id=row.project_run_id,
            source_key=row.source_key,
            identity_key=row.identity_key,
            seen_at=canonical_value(row.seen_at),
            first_seen=canonical_value(row.first_seen),
            record=row.record,
        )

    def _asset_rows(self, session, statement, query):
        filtered = statement.subquery()
        stats = (
            select(
                filtered.c.display_asset_id.label("asset_id"),
                func.min(filtered.c.first_seen).label("first_seen"),
                func.max(filtered.c.seen_at).label("last_seen"),
                func.count().label("observation_count"),
            )
            .group_by(filtered.c.display_asset_id)
            .subquery()
        )
        rows = select(
            ProjectAsset, stats.c.first_seen, stats.c.last_seen, stats.c.observation_count
        ).join(stats, stats.c.asset_id == ProjectAsset.id)
        matched = session.scalar(select(func.count()).select_from(stats))
        order = {"id": ProjectAsset.id, "url": ProjectAsset.url, "last_seen": stats.c.last_seen}[
            query.sort
        ]
        rows = rows.order_by(
            order.desc() if query.order == "desc" else order.asc(), ProjectAsset.id
        )
        selected = session.execute(
            rows.offset((query.page - 1) * query.page_size).limit(query.page_size)
        ).all()
        ids = [r[0].id for r in selected]
        if not ids:
            return [], matched
        # Rank within each identity. Equal-time evidence stays a set, never an arbitrary winner.
        ranked = (
            select(
                filtered,
                func.dense_rank()
                .over(
                    partition_by=[filtered.c.display_asset_id, filtered.c.identity_key],
                    order_by=filtered.c.seen_at.desc(),
                )
                .label("position"),
            )
            .where(filtered.c.display_asset_id.in_(ids))
            .subquery()
        )
        observations = aliased(ProjectObservation, ranked)
        latest = {asset_id: [] for asset_id in ids}
        for row in session.scalars(
            select(observations)
            .where(or_(ranked.c.position == 1, ranked.c.seen_at.is_(None)))
            .order_by(ranked.c.id)
        ):
            latest[row.display_asset_id].append(self._observation(row))
        annotations = {
            a.asset_id: a
            for a in session.scalars(
                select(ProjectAnnotation).where(ProjectAnnotation.asset_id.in_(ids))
            )
        }
        return [
            dict(
                id=a.id,
                url=a.url,
                site=a.site,
                method=a.method,
                kind=a.kind,
                original_type=a.original_type,
                manual=a.manual,
                first_seen=canonical_value(first),
                last_seen=canonical_value(last),
                observation_count=count,
                latest_observations=latest[a.id],
                tags=annotations[a.id].tags if a.id in annotations else [],
                note=annotations[a.id].note if a.id in annotations else "",
            )
            for a, first, last, count in selected
        ], matched

    def list(self, session, project_id, query: ProjectQuery):
        project = ProjectService().get(session, project_id)
        statement = self._observations(project_id, query)
        if query.view == "assets":
            items, matched = self._asset_rows(session, statement, query)
        elif query.view == "observations":
            matched = session.scalar(select(func.count()).select_from(statement.subquery()))
            order = {
                "id": ProjectObservation.id,
                "url": ProjectAsset.url,
                "last_seen": ProjectObservation.seen_at,
            }[query.sort]
            statement = statement.order_by(
                order.desc() if query.order == "desc" else order.asc(), ProjectObservation.id
            )
            items = [
                self._observation(r)
                for r in session.scalars(
                    statement.offset((query.page - 1) * query.page_size).limit(query.page_size)
                )
            ]
        else:
            model = ProjectCandidate if query.view == "candidates" else ProjectRelation
            rows = select(model).where(model.project_id == project_id)
            if query.project_run_id is not None:
                rows = rows.where(model.project_run_id == query.project_run_id)
            if query.view == "relations":
                if query.kind:
                    rows = rows.where(model.kind == query.kind)
                if query.asset_id:
                    rows = rows.where(
                        or_(
                            model.from_asset_id == query.asset_id,
                            model.to_asset_id == query.asset_id,
                        )
                    )
                if query.q:
                    rows = rows.where(
                        func.lower(cast(model.detail, String)).contains(
                            query.q.casefold(), autoescape=True
                        )
                    )
            else:
                for value, column in [
                    (query.asset_id, model.asset_id),
                    (query.original_asset_id, model.asset_id),
                    (query.identity_key, model.identity_key),
                    (query.site, model.record["site"].as_string()),
                    (query.kind, model.record["kind"].as_string()),
                    (query.method, model.record["method"].as_string()),
                ]:
                    if value is not None:
                        rows = rows.where(column == value)
                if query.q:
                    rows = rows.where(
                        func.lower(model.record["url"].as_string()).contains(
                            query.q.casefold(), autoescape=True
                        )
                    )
            if query.view == "candidates":
                if query.seen_from is not None or query.seen_to is not None:
                    # Candidate discovery has no observed response timestamp.
                    rows = rows.where(False)
                if query.tag:
                    tags = func.json_each(ProjectAnnotation.tags).table_valued("value")
                    rows = rows.where(
                        select(ProjectAnnotation.asset_id)
                        .where(
                            ProjectAnnotation.asset_id == model.asset_id,
                            select(1).select_from(tags).where(tags.c.value == query.tag).exists(),
                        )
                        .exists()
                    )
            elif any(
                v is not None
                for v in (
                    query.site,
                    query.method,
                    query.identity_key,
                    query.seen_from,
                    query.seen_to,
                    query.tag,
                    query.original_asset_id,
                )
            ):
                endpoint_query = query.model_copy(update={"kind": None, "asset_id": None, "q": ""})
                endpoint_ids = self._observations(project_id, endpoint_query).with_only_columns(
                    ProjectObservation.asset_id
                )
                rows = rows.where(
                    or_(model.from_asset_id.in_(endpoint_ids), model.to_asset_id.in_(endpoint_ids))
                )
            matched = session.scalar(select(func.count()).select_from(rows.subquery()))
            order = model.id
            if query.sort == "url":
                order = (
                    model.record["url"].as_string()
                    if query.view == "candidates"
                    else model.detail["to_url"].as_string()
                )
            rows = rows.order_by(order.desc() if query.order == "desc" else order, model.id)
            values = session.scalars(
                rows.offset((query.page - 1) * query.page_size).limit(query.page_size)
            )
            if query.view == "relations":
                items = [
                    dict(
                        id=r.id,
                        project_run_id=r.project_run_id,
                        kind=r.kind,
                        from_asset_id=r.from_asset_id,
                        to_asset_id=r.to_asset_id,
                        detail=r.detail,
                    )
                    for r in values
                ]
            else:
                items = [
                    dict(
                        id=r.id,
                        project_run_id=r.project_run_id,
                        source_key=r.source_key,
                        asset_id=r.asset_id,
                        record=r.record,
                    )
                    for r in values
                ]
        evidence = (
            items
            if query.view == "observations"
            else [row for asset in items for row in asset.get("latest_observations", [])]
        )
        self._source_availability(session, evidence)
        return ProjectPage(
            items=items,
            matched=matched,
            counts=self._counts(session, project_id),
            revision=project.revision,
            limitations=[
                "资产主体为路由族；观察来自历史任务，不代表持续在线或业务有效。",
                "时间缺失的观察无法判断先后；同一时间的不同响应均予以保留。",
            ],
        )

    def _source_availability(self, session, items):
        ids = {row["project_run_id"] for row in items}
        if not ids:
            return
        runs = {r.id: r for r in session.scalars(select(ProjectRun).where(ProjectRun.id.in_(ids)))}
        available = set()
        for source, model in [("scan", ScanRun), ("inventory", InventoryRun)]:
            source_ids = [r.run_id for r in runs.values() if r.source == source]
            available.update(
                (source, i)
                for i in session.scalars(select(model.id).where(model.id.in_(source_ids)))
            )
        for row in items:
            run = runs[row["project_run_id"]]
            row["source_available"] = (run.source, run.run_id) in available

    def detail(self, session, project_id, asset_id):
        stored = session.get(ProjectAsset, asset_id)
        if not stored or stored.project_id != project_id:
            raise ResourceNotFoundError("project_asset_not_found")
        assets = self.list(session, project_id, ProjectQuery(asset_id=asset_id))
        history_query = ProjectQuery(view="observations", asset_id=asset_id)
        annotation = session.get(ProjectAnnotation, asset_id)
        if assets.items:
            asset = assets.items[0]
        else:
            asset = dict(
                id=stored.id,
                url=stored.url,
                site=stored.site,
                method=stored.method,
                kind=stored.kind,
                manual=stored.manual,
                alias_or_empty=True,
            )
            history_query = ProjectQuery(view="observations", original_asset_id=asset_id)
        history = self.list(session, project_id, history_query)
        aliases = list(
            session.scalars(
                select(ProjectObservation.asset_id)
                .where(
                    ProjectObservation.project_id == project_id,
                    ProjectObservation.display_asset_id == asset_id,
                    ProjectObservation.asset_id != asset_id,
                )
                .distinct()
                .order_by(ProjectObservation.asset_id)
            )
        )
        corrections = [
            dict(
                id=c.id,
                action=c.action,
                status=c.status,
                reason=c.reason,
                source_asset_ids=c.source_asset_ids,
                target_asset_id=c.target_asset_id,
            )
            for c in session.scalars(
                select(ProjectCorrection)
                .where(ProjectCorrection.project_id == project_id)
                .order_by(ProjectCorrection.id)
            )
            if c.target_asset_id == asset_id or asset_id in c.source_asset_ids
        ]
        relations = self.list(
            session, project_id, ProjectQuery(view="relations", asset_id=asset_id)
        )
        return ProjectAssetDetail(
            asset=asset,
            history=history,
            relations=relations.items,
            annotation={
                "tags": annotation.tags if annotation else [],
                "note": annotation.note if annotation else "",
            },
            aliases=aliases,
            corrections=corrections,
        )

    def iter_rows(self, session, project_id, query):
        page = 1
        while True:
            result = self.list(
                session, project_id, query.model_copy(update={"page": page, "page_size": 200})
            )
            yield from result.items
            if page * 200 >= result.matched:
                break
            page += 1
