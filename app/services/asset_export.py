"""Export the same filtered catalog, independently of list pagination."""

import csv
import io
import json
from typing import Literal

from sqlalchemy.orm import Session

from app.schemas.assets import AssetQuery
from app.services.asset_catalog import AssetCatalogService


def _csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def export_assets(
    session: Session, query: AssetQuery, export_format: Literal["json", "csv"]
) -> str:
    result = AssetCatalogService().select(session, query)
    payload = result.model_dump(mode="json", exclude={"page", "page_size"})
    payload["filters"] = query.model_dump(exclude={"page", "page_size"})
    payload["exported_count"] = len(result.items)
    if export_format == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if export_format != "csv":
        raise ValueError("Unsupported asset export format")
    from app.schemas.assets import AssetRecord

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(AssetRecord.model_fields))
    writer.writeheader()
    for item in payload["items"]:
        writer.writerow({k: _csv_cell(v) for k, v in item.items()})
    return output.getvalue()
