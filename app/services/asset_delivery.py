"""Read-only, revision-bound delivery over existing project projections."""

import base64
import csv
import io
import json
from html import escape
from urllib.parse import urlencode

from app.core.errors import ConflictError, InputValidationError
from app.models.project import ProjectRun
from app.schemas.delivery import DeliveryDocument
from app.schemas.projects import ProjectQuery
from app.services.asset_export import _csv_cell
from app.services.project_catalog import ProjectCatalogService
from app.services.project_comparison import ProjectComparisonService
from app.services.project_export import source_coverage
from app.services.project_source import digest
from app.services.project_transactions import ensure_read_snapshot
from app.services.projects import ProjectService

DATASETS = {"assets", "observations", "variants", "candidates", "relations", "changes"}
FORMATS = {"json", "csv", "markdown", "html", "openapi"}
NOTE = "仅交付已保存的历史证据；未观察不表示不存在，已观察不代表业务有效。"


class AssetDeliveryService:
    def _selection(self, session, project_id, query, dataset, before, after):
        if dataset not in DATASETS:
            raise InputValidationError("未知交付数据集。")
        ensure_read_snapshot(session)
        project = ProjectService().get(session, project_id)
        session.refresh(project)
        filters = query.model_dump(mode="json", exclude={"page", "page_size", "view"})
        filters.update(before_run_id=before, after_run_id=after)
        if dataset == "changes":
            if before is None or after is None:
                raise InputValidationError("变更导出需要明确指定前后项目运行。")
            # Changes have their own two-run scope; do not silently ignore asset filters.
            if any(
                v is not None and v != ""
                for k, v in filters.items()
                if k not in {"sort", "order", "before_run_id", "after_run_id"}
            ):
                raise InputValidationError("变更清单只接受前后项目运行，不接受资产筛选。")
            result = ProjectComparisonService().compare(session, project_id, before, after)
            rows = [
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
                for r in result.items
            ]
            return project.revision, filters, rows, result.limitations
        if before is not None or after is not None:
            raise InputValidationError("前后运行参数仅用于变更清单。")
        catalog = ProjectCatalogService()
        selected = query.model_copy(
            update={"view": "observations" if dataset == "variants" else dataset}
        )
        rows = []
        snapshots = {}
        for row in catalog.iter_rows(session, project_id, selected):
            if dataset == "variants":
                rid = row["project_run_id"]
                if rid not in snapshots:
                    snapshots[rid] = session.get(ProjectRun, rid).snapshot.get(
                        "request_variants", {}
                    )
                variants = snapshots[rid].get(row["source_key"])
                for variant in variants or [None]:
                    rows.append(
                        dict(
                            record_type="variant",
                            id=f"{row['id']}:{variant['variant_id'] if variant else 'unknown'}",
                            observation_id=row["id"],
                            **{
                                k: row[k]
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
                    )
            else:
                rows.append(
                    dict(
                        row,
                        record_type={
                            "assets": "asset",
                            "observations": "observation",
                            "candidates": "candidate",
                            "relations": "relation",
                        }[dataset],
                    )
                )
        return project.revision, filters, rows, [NOTE]

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
        dataset = dataset or query.view
        revision, filters, rows, limitations = self._selection(
            session, project_id, query, dataset, before_run_id, after_run_id
        )
        binding = digest([project_id, dataset, filters, query.page_size])
        offset = 0
        if query.page != 1:
            raise InputValidationError("稳定交付分页请使用 next_cursor；完整导出忽略屏幕页码。")
        if cursor:
            try:
                if len(cursor) > 4096:
                    raise ValueError
                token = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                offset = token["offset"]
                if token["binding"] != binding or type(offset) is not int or offset < 0:
                    raise ValueError
                if token["revision"] != revision:
                    raise ConflictError(
                        "delivery_revision_changed：项目已变化，请从第一页重新读取。"
                    )
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise InputValidationError(
                    "交付游标无效或筛选已改变，请从第一页重新读取。"
                ) from None
        selected = rows if complete else rows[offset : offset + query.page_size]
        next_cursor = None
        if not complete and offset + len(selected) < len(rows):
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    dict(revision=revision, binding=binding, offset=offset + len(selected))
                ).encode()
            ).decode()
        coverage, summary = source_coverage(session, project_id, query.project_run_id)
        if dataset == "changes":
            coverage = [r for r in coverage if r["project_run_id"] in {before_run_id, after_run_id}]
            # The two selected source runs are the coverage scope for this delivery.
            summaries = [
                source_coverage(session, project_id, rid)[1]
                for rid in (before_run_id, after_run_id)
            ]
            summary = {k: sum(s[k] for s in summaries) for k in summaries[0]}
        return DeliveryDocument(
            project_id=project_id,
            revision=revision,
            dataset=dataset,
            filters=filters,
            matched=len(rows),
            exported_count=len(selected),
            next_cursor=next_cursor,
            items=selected,
            source_coverage=coverage,
            coverage_summary=summary,
            limitations=[
                *limitations,
                "覆盖说明独立于记录筛选；分页绑定项目修订，改变后需重新读取。",
            ],
        ).model_dump(mode="json")

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
        if format not in FORMATS:
            raise InputValidationError("交付格式须为 json/csv/markdown/html/openapi。")
        document = self.page(
            session,
            project_id,
            query.model_copy(update={"page": 1}),
            dataset=dataset,
            before_run_id=before_run_id,
            after_run_id=after_run_id,
            complete=True,
        )
        if format == "openapi":
            from app.services.delivery_openapi import openapi_draft

            if document["dataset"] != "observations":
                raise InputValidationError("OpenAPI 草稿需要选择身份观察数据集。")
            return json.dumps(openapi_draft(document), ensure_ascii=False, indent=2) + "\n"
        if format == "json":
            return json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        if format == "csv":
            output = io.StringIO(newline="")
            writer = csv.DictWriter(
                output,
                fieldnames=[
                    "schema_version",
                    "project_id",
                    "revision",
                    "dataset",
                    "matched",
                    "row_kind",
                    "filters",
                    "coverage_summary",
                    "limitations",
                    "record",
                ],
            )
            writer.writeheader()
            metadata = {k: v for k, v in document.items() if k in writer.fieldnames}
            for row in document["items"] or [None]:
                writer.writerow(
                    {
                        k: _csv_cell(v)
                        for k, v in dict(
                            metadata,
                            row_kind="record" if row is not None else "metadata",
                            record=row,
                        ).items()
                    }
                )
            return output.getvalue()
        return summary(document, html=format == "html")


def summary(document, *, html=False):
    pid = document["project_id"]
    filters = {
        k: v
        for k, v in document["filters"].items()
        if v is not None and k not in {"before_run_id", "after_run_id"}
    }
    view = document["dataset"]
    link = f"/projects/{pid}/assets?" + urlencode(
        dict(filters, view="observations" if view == "variants" else view)
    )
    if view == "changes":
        link = f"/projects/{pid}/compare?" + urlencode(
            {k: document["filters"][k] for k in ("before_run_id", "after_run_id")}
        )
    lines = [
        f"项目 {pid} · {view} 交付概要",
        f"格式 delivery-v1 · 修订 {document['revision']}",
        f"全部筛选结果 {document['matched']} 条；此文件只含概要，完整记录见 JSON/CSV。",
        "筛选：" + json.dumps(document["filters"], ensure_ascii=False),
        "覆盖：" + json.dumps(document["coverage_summary"], ensure_ascii=False),
        *document["limitations"],
        "证据链接相对于原 Garden 服务，不包含登录材料。",
    ]
    if html:
        return (
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>Garden 资产交付概要</title><body>"
            + "".join(f"<p>{escape(line)}</p>" for line in lines)
            + f'<a href="{escape(link, quote=True)}">查看原服务证据</a></body></html>'
        )
    return (
        "# Garden 资产交付概要\n\n"
        + "\n\n".join(escape(line) for line in lines)
        + f"\n\n[查看原服务证据]({link})\n"
    )
