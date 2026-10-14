"""Complete redacted queue exports from one read snapshot, including empty selections."""

import csv
import io
import json

from app.core.errors import InputValidationError
from app.models.scan_run import ScanRun
from app.services.asset_export import _csv_cell
from app.services.collection_queue import CollectionQueueService
from app.services.project_transactions import ensure_read_snapshot


def export_collection(session, queue_id, format, *, state=None):
    if format not in {"json", "csv", "markdown"}:
        raise InputValidationError("collection_invalid_export_format")
    ensure_read_snapshot(session)
    service = CollectionQueueService()
    queue = service.get(session, queue_id)
    run = session.get(ScanRun, queue["run_id"])
    first = service.list_work(session, queue_id, state=state, page_size=200)
    items = first["items"]
    for page in range(2, (first["total"] + 199) // 200 + 1):
        items.extend(
            service.list_work(session, queue_id, state=state, page=page, page_size=200)["items"]
        )
    metadata = dict(
        schema_version="1",
        queue_scope="known_work_only",
        filters={"state": state},
        total=first["total"],
        queue=queue,
        transport=(run.options or {}).get("collection_mode", "http"),
        note="未覆盖不表示不存在；队列不代表全站范围。",
    )
    if format == "json":
        return json.dumps(
            {**metadata, "items": items}, ensure_ascii=False, indent=2, allow_nan=False
        )
    if format == "csv":
        output = io.StringIO(newline="")
        fields = ["row_type", "metadata", "id", "url", "method", "state", "reason", "attempts"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "row_type": "metadata",
                "metadata": json.dumps(metadata, ensure_ascii=False, allow_nan=False),
            }
        )
        for item in items:
            writer.writerow(
                {"row_type": "work", **{key: _csv_cell(item.get(key)) for key in fields[2:]}}
            )
        return output.getvalue()
    # Escape markup as data. No clickable URLs or interpreted HTML in work rows.
    import html

    def cell(value):
        return (
            html.escape(str(value if value is not None else "—"))
            .replace("|", "&#124;")
            .replace("\n", " ")
            .replace("`", "&#96;")
        )

    lines = [
        f"# 队列 #{queue_id}",
        metadata["note"],
        "```json\n" + json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n```",
        "| 方法 | 请求 | 状态 | 原因 |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        "| " + " | ".join(cell(item[k]) for k in ("method", "url", "state", "reason")) + " |"
        for item in items
    )
    return "\n\n".join(lines[:3]) + "\n\n" + "\n".join(lines[3:]) + "\n"
