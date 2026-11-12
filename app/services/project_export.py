"""Complete filtered exports from a single explicit database read snapshot."""

import csv
import io

from sqlalchemy import select

from app.models.project import ProjectRun
from app.services.asset_export import _csv_cell
from app.services.project_catalog import ProjectCatalogService
from app.services.project_transactions import ensure_read_snapshot


def source_coverage(session, project_id, project_run_id=None):
    statement = (
        select(
            ProjectRun.id,
            ProjectRun.source,
            ProjectRun.run_id,
            ProjectRun.status,
            ProjectRun.snapshot["scope"],
            ProjectRun.snapshot["comparison_metadata"]["budget"],
        )
        .where(ProjectRun.project_id == project_id)
        .order_by(ProjectRun.id)
    )
    if project_run_id is not None:
        statement = statement.where(ProjectRun.id == project_run_id)
    rows = [
        dict(
            project_run_id=rid,
            source=source,
            run_id=run_id,
            index_status=status,
            scope=scope or {},
            budget=budget,
        )
        for rid, source, run_id, status, scope, budget in session.execute(statement)
    ]
    summary = dict(sources=len(rows), incomplete_sources=0, unknown_sources=0, unindexed_sources=0)
    for row in rows:
        scope = row["scope"]
        summary["unindexed_sources"] += row["index_status"] != "complete"
        if not scope or scope.get("completeness") is None:
            summary["unknown_sources"] += 1
        elif scope.get("completeness") != "complete" or scope.get("status") not in {
            "completed",
            "completed_with_warnings",
        }:
            summary["incomplete_sources"] += 1
    return rows, summary


def export_project(session, project_id, query, format):
    """Preserve the string-returning Python API; HTTP/CLI write directly to files."""
    output = io.StringIO(newline="")
    write_project(session, project_id, query, format, output)
    return output.getvalue()


def write_project(session, project_id, query, format, output):
    if format not in {"json", "csv", "markdown"}:
        raise ValueError("Unsupported project export format")
    ensure_read_snapshot(session)
    catalog = ProjectCatalogService()
    first = catalog.list(session, project_id, query.model_copy(update={"page": 1, "page_size": 1}))
    filters = query.model_dump(mode="json", exclude={"page", "page_size"})
    coverage, coverage_summary = source_coverage(session, project_id, query.project_run_id)
    coverage_scope = "selected_project_run" if query.project_run_id is not None else "whole_project"
    coverage_note = "覆盖说明包含所选项目运行或整个项目，独立于记录筛选；零观察任务也保留。"
    if format == "markdown":
        from app.services.asset_delivery import AssetDeliveryService

        output.write(f"# 项目 {project_id} 资产概要\n\n兼容入口：project-v1。\n\n")
        AssetDeliveryService().write(session, project_id, query, "markdown", output)
        return
    rows = catalog.iter_rows(session, project_id, query)
    metadata = dict(
        schema_version="project-v1",
        project_id=project_id,
        revision=first.revision,
        filters=filters,
        exported_count=first.matched,
        coverage_scope=coverage_scope,
        coverage_summary=coverage_summary,
    )
    if format == "json":
        from app.services.delivery_files import write_json

        write_json(
            output,
            dict(
                **metadata,
                counts=first.counts,
                limitations=[*first.limitations, coverage_note],
                source_coverage=coverage,
            ),
            rows,
        )
        return
    columns = list(first.items[0]) if first.items else []
    writer = csv.DictWriter(output, fieldnames=["row_kind", *metadata, *columns])
    writer.writeheader()
    if not first.matched:
        writer.writerow({"row_kind": "metadata", **{k: _csv_cell(v) for k, v in metadata.items()}})
    for row in rows:
        writer.writerow(
            {"row_kind": "record", **{k: _csv_cell(v) for k, v in {**metadata, **row}.items()}}
        )
