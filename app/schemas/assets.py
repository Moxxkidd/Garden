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
    view: Literal["records", "grouped", "candidates"] = "records"
    kind: AssetKind | None = None
    context: str | None = Field(default=None, max_length=120)
    observation: Observation | None = None
    validity: (
        Literal[
            "login_page",
            "suspected_login_fallback",
            "suspected_soft_404",
            "uniform_response",
            "uniform_error_response",
            "redirect_alias",
            "none",
        ]
        | None
    ) = None
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


class ValidityFlag(BaseModel):
    code: str
    label: str
    reason: str
    related_asset_ids: list[str] = Field(default_factory=list)
    request_ids: list[int] = Field(default_factory=list)


class AssetValidity(BaseModel):
    assessment: Literal["assessed", "insufficient_evidence"] = "insufficient_evidence"
    verification: Literal["response_observed", "candidate", "unknown"] = "unknown"
    business_validity: Literal["unconfirmed"] = "unconfirmed"
    access: Literal["restricted", "unknown"] = "unknown"
    flags: list[ValidityFlag] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    note: str = "被动迹象，不是业务有效性结论；未命中迹象不代表有效或安全。"


class AssetRecord(BaseModel):
    asset_id: str
    validity: AssetValidity = Field(default_factory=AssetValidity)
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


class AssetRequestObservation(BaseModel):
    request_id: int
    source_observation_id: str
    context: str
    status_code: int | None = None
    content_type: str | None = None


class AssetVariant(BaseModel):
    variant_id: str
    identity_status: Literal["known", "unknown"]
    observations: list[AssetRequestObservation] = Field(default_factory=list)
    request_ids: list[int]
    observation_ids: list[str]
    contexts: list[str]
    note: str


class AssetGroup(BaseModel):
    validity_flags: list[ValidityFlag] = Field(default_factory=list)
    asset_id: str
    rule_version: str
    kind: AssetKind
    url: str
    site: str | None
    method: str | None
    title: str | None
    last_seen: datetime | None
    contexts: list[str]
    status_codes: list[int]
    observation_count: int
    variant_count: int | None
    variant_record_count: int
    variants: list[AssetVariant]
    observations: list[AssetRecord]
    evidence_ids: list[int]
    request_ids: list[int]
    grouping_reason: str
    limitations: str


class AssetPage(BaseModel):
    schema_version: str = "1.2"
    validity_rule_version: str = "passive-v1"
    validity_counts: dict[str, int] = Field(default_factory=dict)
    candidate_count: int | None = None
    scope: AssetScope
    total: int
    matched: int
    counts_by_kind: dict[str, int]
    page: int
    page_size: int
    items: list[AssetRecord | AssetGroup]
    view: Literal["records", "grouped", "candidates"] = "records"
    rule_version: str | None = None
    matched_observation_count: int | None = None
    count_note: str = "按已有资产记录计数；未跨身份或跨任务归并，不代表独立业务资产数。"
