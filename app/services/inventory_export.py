"""Inventory export helpers."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from app.models.inventory_run import InventoryRun
from app.schemas.inventory import InventoryExportResult, default_inventory_export_path


class InventoryExportService:
    """Export inventory runs to JSON or CSV."""

    def export_json(self, inventory_run: InventoryRun) -> InventoryExportResult:
        output_path = default_inventory_export_path(inventory_run.id, "json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self._serialize_run(inventory_run), indent=2),
            encoding="utf-8",
        )
        return InventoryExportResult(
            inventory_run_id=inventory_run.id,
            format="json",
            output_path=str(output_path),
        )

    def export_csv(self, inventory_run: InventoryRun) -> InventoryExportResult:
        output_directory = default_inventory_export_path(inventory_run.id, "csv")
        output_directory.mkdir(parents=True, exist_ok=True)

        self._write_csv(
            output_directory / "inventory_run.csv",
            [
                "id",
                "status",
                "target_id",
                "credential_profile_id",
                "auth_session_id",
                "scan_job_id",
                "started_from_url",
                "pages_count",
                "endpoints_count",
                "parameters_count",
                "annotations_count",
                "summary",
            ],
            [
                {
                    "id": inventory_run.id,
                    "status": inventory_run.status,
                    "target_id": inventory_run.target_id,
                    "credential_profile_id": inventory_run.credential_profile_id,
                    "auth_session_id": inventory_run.auth_session_id,
                    "scan_job_id": inventory_run.scan_job_id,
                    "started_from_url": inventory_run.started_from_url,
                    "pages_count": inventory_run.pages_count,
                    "endpoints_count": inventory_run.endpoints_count,
                    "parameters_count": inventory_run.parameters_count,
                    "annotations_count": inventory_run.annotations_count,
                    "summary": inventory_run.summary or "",
                }
            ],
        )
        self._write_csv(
            output_directory / "inventory_pages.csv",
            ["id", "url", "title", "depth", "first_visited_at", "last_visited_at", "visit_count"],
            [
                {
                    "id": page.id,
                    "url": page.url,
                    "title": page.title or "",
                    "depth": page.depth,
                    "first_visited_at": page.first_visited_at.isoformat(),
                    "last_visited_at": page.last_visited_at.isoformat(),
                    "visit_count": page.visit_count,
                }
                for page in inventory_run.pages
            ],
        )
        self._write_csv(
            output_directory / "inventory_endpoints.csv",
            [
                "id",
                "method",
                "url",
                "path",
                "first_seen_at",
                "last_seen_at",
                "request_count",
                "status_codes_observed",
            ],
            [
                {
                    "id": endpoint.id,
                    "method": endpoint.method,
                    "url": endpoint.url,
                    "path": endpoint.path,
                    "first_seen_at": endpoint.first_seen_at.isoformat(),
                    "last_seen_at": endpoint.last_seen_at.isoformat(),
                    "request_count": endpoint.request_count,
                    "status_codes_observed": ",".join(
                        str(code) for code in endpoint.status_codes_observed
                    ),
                }
                for endpoint in inventory_run.endpoints
            ],
        )
        self._write_csv(
            output_directory / "inventory_parameters.csv",
            [
                "id",
                "inventory_endpoint_id",
                "source_type",
                "name",
                "first_seen_at",
                "last_seen_at",
                "seen_count",
                "sensitive",
            ],
            [
                {
                    "id": parameter.id,
                    "inventory_endpoint_id": parameter.inventory_endpoint_id or "",
                    "source_type": parameter.source_type,
                    "name": parameter.name,
                    "first_seen_at": parameter.first_seen_at.isoformat(),
                    "last_seen_at": parameter.last_seen_at.isoformat(),
                    "seen_count": parameter.seen_count,
                    "sensitive": "yes" if parameter.sensitive else "no",
                }
                for parameter in inventory_run.parameters
            ],
        )
        self._write_csv(
            output_directory / "inventory_annotations.csv",
            ["id", "subject_type", "subject_ref", "marker", "reason"],
            [
                {
                    "id": annotation.id,
                    "subject_type": annotation.subject_type,
                    "subject_ref": annotation.subject_ref,
                    "marker": annotation.marker,
                    "reason": annotation.reason,
                }
                for annotation in inventory_run.annotations
            ],
        )
        return InventoryExportResult(
            inventory_run_id=inventory_run.id,
            format="csv",
            output_path=str(output_directory),
        )

    def _serialize_run(self, inventory_run: InventoryRun) -> dict[str, object]:
        return {
            "inventory_run": {
                "id": inventory_run.id,
                "status": inventory_run.status,
                "target_id": inventory_run.target_id,
                "credential_profile_id": inventory_run.credential_profile_id,
                "auth_session_id": inventory_run.auth_session_id,
                "scan_job_id": inventory_run.scan_job_id,
                "started_at": (
                    inventory_run.started_at.isoformat() if inventory_run.started_at else None
                ),
                "finished_at": (
                    inventory_run.finished_at.isoformat() if inventory_run.finished_at else None
                ),
                "started_from_url": inventory_run.started_from_url,
                "include_rules": inventory_run.include_rules,
                "exclude_rules": inventory_run.exclude_rules,
                "counts": {
                    "pages": inventory_run.pages_count,
                    "endpoints": inventory_run.endpoints_count,
                    "parameters": inventory_run.parameters_count,
                    "annotations": inventory_run.annotations_count,
                },
                "summary": inventory_run.summary,
                "error_message": inventory_run.error_message,
            },
            "pages": [
                {
                    "url": page.url,
                    "title": page.title,
                    "depth": page.depth,
                    "first_visited_at": page.first_visited_at.isoformat(),
                    "last_visited_at": page.last_visited_at.isoformat(),
                    "visit_count": page.visit_count,
                }
                for page in inventory_run.pages
            ],
            "endpoints": [
                {
                    "method": endpoint.method,
                    "url": endpoint.url,
                    "path": endpoint.path,
                    "status_codes_observed": endpoint.status_codes_observed,
                    "request_count": endpoint.request_count,
                }
                for endpoint in inventory_run.endpoints
            ],
            "parameters": [
                {
                    "inventory_endpoint_id": parameter.inventory_endpoint_id,
                    "source_type": parameter.source_type,
                    "name": parameter.name,
                    "seen_count": parameter.seen_count,
                    "sensitive": parameter.sensitive,
                }
                for parameter in inventory_run.parameters
            ],
            "annotations": [
                {
                    "subject_type": annotation.subject_type,
                    "subject_ref": annotation.subject_ref,
                    "marker": annotation.marker,
                    "reason": annotation.reason,
                }
                for annotation in inventory_run.annotations
            ],
        }

    def _write_csv(
        self,
        path: Path,
        fieldnames: list[str],
        rows: list[dict[str, object]],
    ) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
