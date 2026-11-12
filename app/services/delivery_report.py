"""One public report vocabulary over the selected, revision-bound asset view."""

from collections import Counter

from sqlalchemy import func, select

from app.models.project_asset import ProjectAsset
from app.models.project_curation import ProjectAnnotation
from app.services.project_catalog import ProjectCatalogService

GLOSSARY = [
    "主体是路由族，观察是一次来源任务和身份下保存的响应；两者数量不可互换。",
    "已观察不代表业务有效；人工保留是整理决定，不是机器验证结论。",
    "未知、未覆盖或未再次观察均不表示不存在、无权限或已删除。",
]
KIND_LABELS = {
    "page": "页面",
    "endpoint": "接口",
    "static": "静态资源",
    "file": "文件",
    "other": "其他",
}


def project_report(session, project_id, query, dataset, matched, coverage, coverage_summary):
    summary = dict(
        matched_records=matched,
        asset_count=None,
        observation_count=None,
        categories={},
        identities=[],
        review={},
        execution=dict(Counter(r["scope"].get("status", "unknown") for r in coverage)),
        coverage=coverage_summary,
    )
    if dataset in {"assets", "observations", "variants"}:
        filtered = ProjectCatalogService()._observations(project_id, query).subquery()
        summary["asset_count"], summary["observation_count"] = session.execute(
            select(func.count(func.distinct(filtered.c.display_asset_id)), func.count())
        ).one()
        summary["categories"] = dict(
            session.execute(
                select(ProjectAsset.kind, func.count(func.distinct(filtered.c.display_asset_id)))
                .join(ProjectAsset, ProjectAsset.id == filtered.c.display_asset_id)
                .group_by(ProjectAsset.kind)
            ).all()
        )
        summary["identities"] = [
            dict(key=key, name=name, observation_count=count)
            for key, name, count in session.execute(
                select(
                    filtered.c.identity_key,
                    func.max(filtered.c.record["display_name"].as_string()),
                    func.count(),
                )
                .group_by(filtered.c.identity_key)
                .order_by(filtered.c.identity_key)
            )
        ]
        ids = select(filtered.c.display_asset_id).distinct().subquery()
        state = func.coalesce(ProjectAnnotation.review_state, "unreviewed")
        reviews = dict(
            session.execute(
                select(state, func.count())
                .select_from(ids)
                .outerjoin(ProjectAnnotation, ProjectAnnotation.asset_id == ids.c.display_asset_id)
                .group_by(state)
            ).all()
        )
        summary["review"] = {s: reviews.get(s, 0) for s in ("keep", "ignore", "unreviewed")}
        summary["review"]["needs_review"] = session.scalar(
            select(func.count())
            .select_from(ids)
            .join(ProjectAnnotation, ProjectAnnotation.asset_id == ids.c.display_asset_id)
            .where(ProjectAnnotation.needs_review.is_(True))
        )
    return dict(
        summary=summary,
        assets=dict(
            dataset=dataset,
            matched_records=matched,
            filters=query.model_dump(mode="json", exclude={"page", "page_size"}),
        ),
        evidence=dict(
            content="public_references_only",
            sources=coverage,
            not_included=[
                "原始响应正文、截图和私有会话文件未包含；公开引用须回原 Garden 服务查看。"
            ],
        ),
        next_steps=[
            "先处理来源覆盖缺口，再复核所需资产。"
            if any(
                coverage_summary[k]
                for k in ("incomplete_sources", "unknown_sources", "unindexed_sources")
            )
            else "按实际业务需求复核清单；执行结束不表示全站覆盖。"
        ],
        glossary=GLOSSARY,
    )


def summary_lines(document):
    report = document["report"]
    s = report["summary"]
    lines = [f"全部筛选结果 {document['matched']} 条 · 项目修订 {document['revision']}"]
    if s["asset_count"] is not None:
        lines += [f"{s['asset_count']} 个主体，{s['observation_count']} 条观察"]
        lines += [
            "资产分类："
            + " · ".join(f"{KIND_LABELS.get(k, k)} {v}" for k, v in s["categories"].items())
        ]
        lines += [
            f"人工保留 {s['review']['keep']} · 忽略 {s['review']['ignore']} · "
            f"未复核 {s['review']['unreviewed']} · 需再复核 {s['review']['needs_review']}"
        ]
        lines += [
            "身份："
            + " · ".join(
                f"{i['name'] or i['key']}（{i['observation_count']} 条观察）"
                for i in s["identities"]
            )
        ]
    c = s["coverage"]
    lines += [
        f"覆盖来源 {c['sources']} · 不完整 {c['incomplete_sources']} · "
        f"未知 {c['unknown_sources']} · 未入库 {c['unindexed_sources']}"
    ]
    return lines + report["next_steps"]
