"""Read-only source projection. Private request/session material is never opened."""

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.core.errors import ConflictError, ResourceNotFoundError
from app.models.inventory_run import InventoryRun
from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES, ScanRun
from app.schemas.assets import AssetQuery
from app.schemas.projects import ProjectSourceSnapshot
from app.services.asset_catalog import AssetCatalogService, safe_url
from app.services.projects import ProjectService, project_origin, project_url


def canonical_time(value):
    if value is None:
        return None
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def canonical_value(value):
    if isinstance(value, datetime):
        return canonical_time(value).isoformat()
    if isinstance(value, dict):
        return {k: canonical_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical_value(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(
        json.dumps(
            canonical_value(value),
            sort_keys=True,
            ensure_ascii=True,
            default=str,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


class ProjectSourceService:
    def source(self, session, ref):
        run = session.get(ScanRun if ref.source == "scan" else InventoryRun, ref.run_id)
        if run is None:
            raise ResourceNotFoundError("project_source_missing")
        return run

    def entry_digest(self, ref, run):
        if ref.source == "scan" and run.entry_url_digest:
            return run.entry_url_digest
        url = run.normalized_url if ref.source == "scan" else run.started_from_url
        if ref.source == "scan" and run.mode == "identity_collection" and urlsplit(url).query:
            # Legacy redacted query values cannot prove an exact entry binding.
            return None
        return digest(project_url(url))

    def terminal(self, ref, run):
        return run.status in (
            TERMINAL_SCAN_RUN_STATUSES
            if ref.source == "scan"
            else {
                "completed",
                "failed",
                "cancelled",
                "interrupted",
                "incomplete",
                "completed_with_warnings",
            }
        )

    def snapshot(self, session, ref):
        run = self.source(session, ref)
        if not self.terminal(ref, run):
            raise ConflictError("source_not_terminal")
        service = AssetCatalogService()
        query = AssetQuery(source=ref.source, run_id=ref.run_id)
        result = service.select(session, query)
        candidates = (
            service.select(session, query.model_copy(update={"view": "candidates"})).items
            if ref.source == "scan"
            else []
        )
        records = result.items
        if ref.source == "inventory":
            records = [
                r.model_copy(update={"profile_id": run.credential_profile_id}) for r in records
            ]
        metadata = {
            "configuration": digest(
                run.options
                if ref.source == "scan"
                else {
                    k: getattr(run, k)
                    for k in [
                        "max_pages",
                        "max_depth",
                        "max_requests",
                        "delay_ms",
                        "include_rules",
                        "exclude_rules",
                    ]
                }
            ),
            "entry": self.entry_digest(ref, run),
            "recovery": bool(getattr(run, "parent_run_id", None)),
            "finished_at": canonical_value(run.finished_at),
            "rule_version": "project-route-v1",
        }
        relations = []
        if ref.source == "scan":
            evidence_by_asset = defaultdict(list)
            for evidence in run.evidence:
                if isinstance(evidence.data, dict):
                    evidence_by_asset[evidence.asset_id].append((evidence.data, evidence.id))
            for asset in run.assets:
                sources = [(asset.attributes or {}, None)]
                sources.extend(evidence_by_asset[asset.id])
                for data, evidence_id in sources:
                    pairs = data.get("catalog_redirects", [])
                    if not isinstance(pairs, list):
                        continue
                    for pair in pairs:
                        if (
                            not isinstance(pair, dict)
                            or not pair.get("requested_url")
                            or not pair.get("final_url")
                        ):
                            continue
                        relations.append(
                            dict(
                                kind="redirect",
                                from_url=safe_url(pair["requested_url"]),
                                to_url=safe_url(pair["final_url"]),
                                source_key=f"scan:{run.id}:asset:{asset.id}",
                                evidence_id=evidence_id,
                            )
                        )
        value = dict(
            scope=result.scope,
            records=records,
            candidates=candidates,
            comparison_metadata=metadata,
            relations=relations,
        )
        material = {
            "scope": result.scope.model_dump(),
            "records": [r.model_dump() for r in records],
            "candidates": [r.model_dump() for r in candidates],
            "metadata": metadata,
            "relations": relations,
        }
        return ProjectSourceSnapshot(**value, digest=digest(material))

    def validate_entry(self, session, project_id, entry_id, snapshot):
        entry = ProjectService().validate_entry(session, project_id, entry_id)
        if snapshot.scope.target_id is not None and snapshot.scope.target_id != entry.target_id:
            raise ConflictError("source_target_mismatch")
        if project_origin(snapshot.scope.entry_url) != entry.origin:
            raise ConflictError("source_origin_mismatch")
        if snapshot.comparison_metadata["entry"] != digest(project_url(entry.entry_url)):
            raise ConflictError("source_entry_mismatch")
        return entry
