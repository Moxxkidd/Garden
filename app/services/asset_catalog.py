"""Read-only adapters over scan and inventory records, with one selection contract."""

from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ResourceNotFoundError
from app.models.inventory_run import InventoryRun
from app.models.scan_run import ScanEvidence, ScanRun
from app.redaction.service import RedactionService
from app.schemas.assets import AssetContext, AssetPage, AssetQuery, AssetRecord, AssetScope
from app.services.coverage_identity import redacted_observed_url
from app.services.scan_result_presentation import coverage_summary

KINDS = {"page": "页面", "endpoint": "接口", "static": "静态资源", "file": "文件", "other": "其他"}
OBSERVATIONS = {"response_observed": "已有响应", "unknown": "未知"}
_KIND_MAP = {
    "page": "page",
    "endpoint": "endpoint",
    "script": "static",
    "stylesheet": "static",
    "image": "static",
    "font": "static",
    "media": "static",
    "document": "file",
    "file": "file",
}
_REDACTOR = RedactionService()
_URL_IN_TEXT = re.compile(r'https?://[^\s<>"\']+', re.I)


def safe_url(value: str | None) -> str:
    try:
        if value and re.search(r"[\x00-\x1f\x7f-\x9f]", value):
            return "[URL 含控制字符，未展示]"
        parsed = urlsplit(value or "")
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return "[URL 未提供或不可解析]"
        return redacted_observed_url(value)
    except (ValueError, TypeError):
        return "[URL 未提供或不可解析]"


def safe_text(value: object, *, limit: int = 500) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    text = _URL_IN_TEXT.sub(lambda m: safe_url(m.group()), value)
    return _REDACTOR.redact_text(text, limit=limit)


def _codes(values) -> list[int]:
    return sorted({v for v in values if type(v) is int and 100 <= v <= 599})


def _site(url: str) -> str | None:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else None


class AssetCatalogService:
    """One source run per query; no network, mutation or protected-payload reads."""

    def list(self, session: Session, query: AssetQuery) -> AssetPage:
        result = self.select(session, query)
        start = (query.page - 1) * query.page_size
        return result.model_copy(update={"items": result.items[start : start + query.page_size]})

    def select(self, session: Session, query: AssetQuery) -> AssetPage:
        scope, rows = (
            self._scan(session, query.run_id)
            if query.source == "scan"
            else self._inventory(session, query.run_id)
        )
        counts = {kind: sum(r.kind == kind for r in rows) for kind in KINDS}
        search = query.q.strip().casefold()
        filtered = [
            r
            for r in rows
            if (query.kind is None or r.kind == query.kind)
            and (query.context is None or r.context == query.context)
            and (query.observation is None or r.observation == query.observation)
            and (not search or search in f"{r.url} {r.title or ''} {r.method or ''}".casefold())
        ]

        def key(row):
            stable_id = (row.record_type, row.record_id)
            if query.sort == "url":
                return row.url, stable_id
            if query.sort == "type":
                return row.kind, stable_id
            if query.sort == "last_seen":
                return row.last_seen.isoformat() if row.last_seen else "", stable_id
            return stable_id

        filtered.sort(key=key, reverse=query.order == "desc")
        return AssetPage(
            scope=scope,
            total=len(rows),
            matched=len(filtered),
            counts_by_kind=counts,
            page=query.page,
            page_size=query.page_size,
            items=filtered,
        )

    def _scan(self, session: Session, run_id: int) -> tuple[AssetScope, list[AssetRecord]]:
        run = session.scalar(
            select(ScanRun)
            .where(ScanRun.id == run_id)
            .options(
                selectinload(ScanRun.assets),
                selectinload(ScanRun.contexts),
                selectinload(ScanRun.requests),
            )
        )
        if run is None:
            raise ResourceNotFoundError(f"Scan {run_id} was not found.")
        context_by_id = {c.id: c for c in run.contexts}
        request_ids = defaultdict(list)
        for request in run.requests:
            if request.asset_id is not None:
                request_ids[request.asset_id].append(request.id)
        evidence_ids = defaultdict(list)
        for asset_id, evidence_id in session.execute(
            select(ScanEvidence.asset_id, ScanEvidence.id).where(ScanEvidence.scan_run_id == run_id)
        ):
            if asset_id is not None:
                evidence_ids[asset_id].append(evidence_id)
        source_url = f"/scans/{run_id}"
        scope = AssetScope(
            source="scan",
            run_id=run_id,
            target_id=run.target_id,
            entry_url=safe_url(run.normalized_url),
            status=run.status,
            completeness=run.completeness,
            coverage_note=coverage_summary(run.status, run.completeness, run.mode),
            source_url=source_url,
            live=run.status in {"queued", "running"},
            contexts=[
                AssetContext(kind=c.kind, status=c.collection_status, completeness=c.completeness)
                for c in run.contexts
            ],
        )
        rows = []
        for asset in run.assets:
            context = context_by_id.get(asset.context_id)
            kind = context.kind if context else ("anonymous" if run.mode == "quick" else "unknown")
            url = safe_url(asset.url)
            codes = _codes([asset.status_code])
            attrs = asset.attributes if isinstance(asset.attributes, dict) else {}
            requests = sorted(request_ids[asset.id])
            rows.append(
                AssetRecord(
                    asset_id=f"scan:{run_id}:asset:{asset.id}",
                    record_id=asset.id,
                    record_type="scan_asset",
                    kind=_KIND_MAP.get(asset.asset_type, "other"),
                    original_type=safe_text(asset.asset_type) or "unknown",
                    url=url,
                    site=_site(url),
                    method=safe_text(asset.method),
                    title=safe_text(asset.title),
                    content_type=safe_text(attrs.get("content_type")),
                    status_codes=codes,
                    observation="response_observed" if codes else "unknown",
                    context=kind,
                    context_completeness=context.completeness if context else None,
                    first_seen=asset.discovered_at,
                    last_seen=asset.discovered_at,
                    request_ids=requests or None,
                    request_count=len(requests) if requests else None,
                    evidence_ids=sorted(evidence_ids[asset.id]),
                    source_url=source_url,
                    provenance_note=(
                        "来自已有扫描资产记录；可按关联证据和请求 ID 追溯。原始发现链未记录。"
                    ),
                )
            )
        return scope, rows

    def _inventory(self, session: Session, run_id: int) -> tuple[AssetScope, list[AssetRecord]]:
        run = session.scalar(
            select(InventoryRun)
            .where(InventoryRun.id == run_id)
            .options(
                selectinload(InventoryRun.pages),
                selectinload(InventoryRun.endpoints),
                selectinload(InventoryRun.credential_profile),
            )
        )
        if run is None:
            raise ResourceNotFoundError(f"Inventory {run_id} was not found.")
        role = safe_text(run.credential_profile.role) if run.credential_profile else None
        role = role or "unknown"
        source_url = f"/inventory/{run_id}"
        scope = AssetScope(
            source="inventory",
            run_id=run_id,
            target_id=run.target_id,
            entry_url=safe_url(run.started_from_url),
            status=run.status,
            coverage_note="覆盖完整性未知：旧 inventory 未提供可确认的完整性信息。",
            source_url=source_url,
            live=run.status in {"queued", "pending", "running"},
            contexts=[AssetContext(kind=role, status="unknown")],
        )
        rows = []
        for kind, records in [("page", run.pages), ("endpoint", run.endpoints)]:
            for record in records:
                url = safe_url(record.url)
                codes = _codes(
                    [record.status_code] if kind == "page" else (record.status_codes_observed or [])
                )
                rows.append(
                    AssetRecord(
                        asset_id=f"inventory:{run_id}:{kind}:{record.id}",
                        record_id=record.id,
                        record_type=f"inventory_{kind}",
                        kind=kind,
                        original_type=kind,
                        url=url,
                        site=_site(url),
                        method=None if kind == "page" else safe_text(record.method),
                        title=safe_text(record.title) if kind == "page" else None,
                        content_type=None if kind == "page" else safe_text(record.content_type),
                        status_codes=codes,
                        observation="response_observed" if codes else "unknown",
                        context=role,
                        first_seen=record.first_visited_at
                        if kind == "page"
                        else record.first_seen_at,
                        last_seen=record.last_visited_at if kind == "page" else record.last_seen_at,
                        request_count=None if kind == "page" else record.request_count,
                        source_url=source_url,
                        provenance_note="来自已有 inventory 记录；未记录逐项证据关联和原始发现链。",
                    )
                )
        return scope, rows
