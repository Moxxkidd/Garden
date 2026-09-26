"""Versioned, display-safe view of existing per-run asset records."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AssetSource = Literal["scan", "inventory"]
AssetKind = Literal["page", "endpoint", "static", "file", "other"]
Observation = Literal["response_observed", "unknown"]


class AssetQuery(BaseModel):
    source: AssetSource
    run_id: int = Field(gt=0)
    kind: AssetKind | None = None
    context: str | None = Field(default=None, max_length=120)
    observation: Observation | None = None
    q: str = Field(default="", max_length=200)
    sort: Literal["id", "url", "type", "last_seen"] = "id"
    order: Literal["asc", "desc"] = "asc"
    page: int = Field(default=1, ge=1, le=1000000)
    page_size: int = Field(default=50, ge=1, le=200)


class AssetContext(BaseModel):
    kind: str
    status: str
    completeness: str | None = None


class AssetScope(BaseModel):
    source: AssetSource
    run_id: int
    target_id: int | None = None
    entry_url: str
    status: str
    completeness: str | None = None
    coverage_note: str
    contexts: list[AssetContext] = Field(default_factory=list)
    source_url: str
    live: bool = False


class AssetRecord(BaseModel):
    asset_id: str
    record_id: int
    record_type: str
    kind: AssetKind
    original_type: str
    url: str
    site: str | None = None
    method: str | None = None
    title: str | None = None
    content_type: str | None = None
    status_codes: list[int]
    observation: Observation
    context: str
    context_completeness: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    request_count: int | None = None
    request_ids: list[int] | None = None
    evidence_ids: list[int] | None = None
    discovery_url: str | None = None
    provenance_note: str
    source_url: str


class AssetPage(BaseModel):
    schema_version: str = "1.0"
    scope: AssetScope
    total: int
    matched: int
    counts_by_kind: dict[str, int]
    page: int
    page_size: int
    items: list[AssetRecord]
    count_note: str = "按已有资产记录计数；未跨身份或跨任务归并，不代表独立业务资产数。"
