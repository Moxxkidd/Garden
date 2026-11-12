"""Revision-bound delivery; records come from SQL-sized catalog chunks."""

import base64
import io
import json
from itertools import islice

from pydantic import TypeAdapter
from sqlalchemy import func, select, true

from app.core.errors import ConflictError, InputValidationError
from app.models.project import ProjectRun
from app.schemas.delivery import DeliveryDocument, DeliveryRow
from app.schemas.projects import ProjectQuery
from app.services.delivery_report import project_report
from app.services.project_catalog import ProjectCatalogService
from app.services.project_comparison import ProjectComparisonService
from app.services.project_export import source_coverage
from app.services.project_source import digest
from app.services.project_transactions import ensure_read_snapshot
from app.services.projects import ProjectService

DATASETS = {"assets", "observations", "variants", "candidates", "relations", "changes"}
FORMATS = {"json", "csv", "readable_csv", "markdown", "html", "openapi"}
NOTE = "仅交付已保存的历史证据；未观察不表示不存在，已观察不代表业务有效。"
ROW = TypeAdapter(DeliveryRow)


class AssetDeliveryService:
    def _validate(self, query, dataset, before, after):
        if dataset not in DATASETS:
            raise InputValidationError("未知交付数据集。")
        filters = query.model_dump(mode="json", exclude={"page", "page_size", "view"})
        filters.update(before_run_id=before, after_run_id=after)
        if dataset == "changes":
            if before is None or after is None:
                raise InputValidationError("变更导出需要明确指定前后项目运行。")
            if any(
                v is not None and v != "" and not (k == "collection" and v == "all")
                for k, v in filters.items()
                if k not in {"sort", "order", "before_run_id", "after_run_id"}
            ):
                raise InputValidationError("变更清单只接受前后项目运行，不接受资产筛选。")
        elif before is not None or after is not None:
            raise InputValidationError("前后运行参数仅用于变更清单。")
        return filters

    def _variants(self, project_id, query):
        filtered = ProjectCatalogService()._observations(project_id, query).subquery()
        mapping = (
            func.json_each(ProjectRun.snapshot, "$.request_variants")
            .table_valued("key", "value")
            .alias("variant_map")
        )
        variants = func.json_each(mapping.c.value).table_valued("key", "value").alias("variants")
        return (
            select(
                filtered,
                variants.c.key.label("variant_index"),
                variants.c.value.label("variant_value"),
            )
            .join(ProjectRun, ProjectRun.id == filtered.c.project_run_id)
            .outerjoin(mapping, mapping.c.key == filtered.c.source_key)
            .outerjoin(variants, true())
        )

    def metadata(
        self, session, project_id, query, *, dataset=None, before_run_id=None, after_run_id=None
    ):
        dataset = dataset or query.view
        filters = self._validate(query, dataset, before_run_id, after_run_id)
        ensure_read_snapshot(session)
        project = ProjectService().get(session, project_id)
        session.refresh(project)
        limitations = [NOTE]
        if dataset == "changes":
            comparison = ProjectComparisonService()
            limitations = comparison.compare(
                session, project_id, before_run_id, after_run_id, keys=[]
            ).limitations
            matched = session.scalar(
                select(func.count()).select_from(
                    comparison.keys_statement(project_id, before_run_id, after_run_id)
                    .order_by(None)
                    .subquery()
                )
            )
        elif dataset == "variants":
            matched = session.scalar(
                select(func.count()).select_from(self._variants(project_id, query).subquery())
            )
        else:
            matched = (
                ProjectCatalogService()
                .list(
                    session,
                    project_id,
                    query.model_copy(update={"view": dataset, "page": 1, "page_size": 1}),
                    include_latest=False,
                )
                .matched
            )
        coverage, coverage_summary = source_coverage(session, project_id, query.project_run_id)
        if dataset == "changes":
            coverage = [r for r in coverage if r["project_run_id"] in {before_run_id, after_run_id}]
            summaries = [
                source_coverage(session, project_id, rid)[1]
                for rid in (before_run_id, after_run_id)
            ]
            coverage_summary = {k: sum(s[k] for s in summaries) for k in summaries[0]}
        return dict(
            schema_version="delivery-v1",
            project_id=project_id,
            revision=project.revision,
            dataset=dataset,
            filters=filters,
            matched=matched,
            exported_count=matched,
            next_cursor=None,
            source_coverage=coverage,
            coverage_summary=coverage_summary,
            limitations=[
                *limitations,
                "覆盖说明独立于记录筛选；分页绑定项目修订，改变后需重新读取。",
            ],
            report=project_report(
                session, project_id, query, dataset, matched, coverage, coverage_summary
            ),
        )

    def iter_rows(
        self,
        session,
        project_id,
        query,
        *,
        dataset=None,
        before_run_id=None,
        after_run_id=None,
        offset=0,
    ):
        dataset = dataset or query.view
        if dataset == "changes":
            for r in ProjectComparisonService().iter_items(
                session, project_id, before_run_id, after_run_id, offset=offset
            ):
                yield ROW.validate_python(
                    dict(
                        record_type="change",
                        id=f"{r['asset_id']}:{r['identity_key']}",
                        **{
                            k: r[k]
                            for k in (
                                "asset_id",
                                "identity_key",
                                "status",
                                "observed_attributes_differ",
                                "reasons",
                            )
                        },
                        before_observation_ids=[o["id"] for o in r["before"]],
                        after_observation_ids=[o["id"] for o in r["after"]],
                    )
                ).model_dump(mode="json")
            return
        if dataset == "variants":
            statement = self._variants(project_id, query)
            cols = statement.selected_columns
            order = {
                "id": cols.id,
                "url": cols.record["url"].as_string(),
                "last_seen": cols.seen_at,
            }[query.sort]
            statement = statement.order_by(
                order.desc() if query.order == "desc" else order, cols.id, cols.variant_index
            )
            while True:
                values = session.execute(statement.offset(offset).limit(200)).mappings().all()
                if not values:
                    break
                for r in values:
                    variant = json.loads(r["variant_value"]) if r["variant_value"] else None
                    yield ROW.validate_python(
                        dict(
                            record_type="variant",
                            id=f"{r['id']}:{variant['variant_id'] if variant else 'unknown'}",
                            observation_id=r["id"],
                            **{
                                k: r[k]
                                for k in (
                                    "asset_id",
                                    "display_asset_id",
                                    "project_run_id",
                                    "identity_key",
                                )
                            },
                            variant=variant,
                            identity_status=variant["identity_status"] if variant else "unknown",
                            note="变体仅在来源任务和身份内识别，不跨任务推断等价。"
                            if variant
                            else "旧快照未保存请求变体；数量与确切请求身份未知。",
                        )
                    ).model_dump(mode="json")
                offset += len(values)
            return
        catalog = ProjectCatalogService()
        selected = query.model_copy(update={"view": dataset})
        skip = offset % 200
        for chunk in catalog.iter_pages(
            session, project_id, selected, include_latest=False, start_page=offset // 200 + 1
        ):
            values = chunk.items[skip:]
            skip = 0
            ids = [
                r.get("display_asset_id") or (r["id"] if dataset == "assets" else r.get("asset_id"))
                for r in values
            ]
            reviews = catalog.reviews(session, project_id, [i for i in ids if i])
            for r, aid in zip(values, ids, strict=True):
                value = dict(
                    r,
                    record_type={
                        "assets": "asset",
                        "observations": "observation",
                        "candidates": "candidate",
                        "relations": "relation",
                    }[dataset],
                )
                if aid in reviews:
                    value["review"] = reviews[aid]
                yield ROW.validate_python(value).model_dump(mode="json")

    def page(
        self,
        session,
        project_id,
        query: ProjectQuery,
        *,
        dataset=None,
        cursor=None,
        before_run_id=None,
        after_run_id=None,
        complete=False,
    ):
        if query.page != 1:
            raise InputValidationError("稳定交付分页请使用 next_cursor；完整导出忽略屏幕页码。")
        args = dict(dataset=dataset, before_run_id=before_run_id, after_run_id=after_run_id)
        document = self.metadata(session, project_id, query, **args)
        binding = digest([project_id, document["dataset"], document["filters"], query.page_size])
        offset = 0
        if cursor:
            try:
                if len(cursor) > 4096:
                    raise ValueError
                token = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                offset = token["offset"]
                if token["binding"] != binding or type(offset) is not int or offset < 0:
                    raise ValueError
                if token["revision"] != document["revision"]:
                    raise ConflictError(
                        "delivery_revision_changed：项目已变化，请从第一页重新读取。"
                    )
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise InputValidationError(
                    "交付游标无效或筛选已改变，请从第一页重新读取。"
                ) from None
        rows = self.iter_rows(session, project_id, query, offset=offset, **args)
        selected = list(rows if complete else islice(rows, query.page_size))
        document["exported_count"] = len(selected)
        if not complete and offset + len(selected) < document["matched"]:
            document["next_cursor"] = base64.urlsafe_b64encode(
                json.dumps(
                    dict(
                        revision=document["revision"],
                        binding=binding,
                        offset=offset + len(selected),
                    )
                ).encode()
            ).decode()
        return DeliveryDocument(**document, items=selected).model_dump(mode="json")

    def export(
        self,
        session,
        project_id,
        query,
        format,
        *,
        dataset=None,
        before_run_id=None,
        after_run_id=None,
    ):
        """String compatibility boundary; large downloads use ExportJobService files."""
        output = io.StringIO(newline="")
        self.write(
            session,
            project_id,
            query,
            format,
            output,
            dataset=dataset,
            before_run_id=before_run_id,
            after_run_id=after_run_id,
        )
        return output.getvalue()

    def write(
        self,
        session,
        project_id,
        query,
        format,
        output,
        *,
        dataset=None,
        before_run_id=None,
        after_run_id=None,
    ):
        from app.services.delivery_files import write_text

        if format not in FORMATS:
            raise InputValidationError("未知交付格式。")
        args = dict(dataset=dataset, before_run_id=before_run_id, after_run_id=after_run_id)
        document = self.metadata(session, project_id, query, **args)
        rows = self.iter_rows(session, project_id, query, **args)
        if format == "readable_csv" and document["dataset"] == "assets":
            rows = self.iter_rows(session, project_id, query, dataset="observations")
        write_text(output, document, rows, format)
