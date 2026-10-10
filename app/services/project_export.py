"""Complete filtered exports from a single explicit database read snapshot."""

import csv
import io
import json

from app.services.asset_export import _csv_cell
from app.services.project_catalog import ProjectCatalogService
from app.services.project_transactions import ensure_read_snapshot


def export_project(session, project_id, query, format):
    if format not in {"json", "csv", "markdown"}:
        raise ValueError("Unsupported project export format")
    ensure_read_snapshot(session)
    catalog = ProjectCatalogService()
    first = catalog.list(session, project_id, query)
    filters = query.model_dump(mode="json", exclude={"page", "page_size"})
    if format == "markdown":
        return (
            f"# 项目 {project_id} 资产概要\n\n"
            f"版本：project-v1；项目修订：{first.revision}\n\n"
            f"当前视图：{query.view}；筛选后记录：{first.matched}\n\n"
            f"项目累计：{first.counts['assets']} 个主体，{first.counts['observations']} 条观察，"
            f"{first.counts['candidates']} 条候选，{first.counts['relations']} 条关系。\n\n"
            "此文件仅含概要；完整记录请导出 JSON 或 CSV。\n\n"
            + "\n".join(f"- {note}" for note in first.limitations)
            + "\n"
        )
    rows = list(catalog.iter_rows(session, project_id, query))
    metadata = dict(
        schema_version="project-v1",
        project_id=project_id,
        revision=first.revision,
        filters=filters,
        exported_count=len(rows),
    )
    if format == "json":
        return (
            json.dumps(
                dict(**metadata, counts=first.counts, limitations=first.limitations, items=rows),
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    output = io.StringIO(newline="")
    columns = list(dict.fromkeys(k for row in rows for k in row))
    writer = csv.DictWriter(output, fieldnames=list(metadata) + columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _csv_cell(v) for k, v in {**metadata, **row}.items()})
    return output.getvalue()
