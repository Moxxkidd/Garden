"""Read-only comparison of persisted passive observations."""

from typing import Literal

from pydantic import BaseModel, Field

ChangeStatus = Literal["new", "persistent", "not_observed", "unknown"]


class ComparisonRun(BaseModel):
    id: int
    entry_display: str
    status: str
    coverage: str


class ComparisonReason(BaseModel):
    code: str
    message: str


class EvidenceReference(BaseModel):
    id: int
    source_url: str
    summary: str


class ObservationReference(BaseModel):
    id: int
    run_id: int
    title: str
    category: str
    severity: str
    confidence: str
    summary: str
    asset_urls: list[str]
    evidence: list[EvidenceReference]
    evidence_missing: bool = False


class ObservationChange(BaseModel):
    status: ChangeStatus
    reason: str
    before: ObservationReference | None = None
    after: ObservationReference | None = None


class ConfigurationComparison(BaseModel):
    field: str
    label: str
    source: str
    current: str
    changed: bool


class ScanComparison(BaseModel):
    source: ComparisonRun
    current: ComparisonRun
    comparable: bool
    configuration: list[ConfigurationComparison] = Field(default_factory=list)
    reasons: list[ComparisonReason] = Field(default_factory=list)
    counts: dict[ChangeStatus, int]
    items: list[ObservationChange]
    notice: str = "未再观察到不代表已修复；本对比不判断漏洞新增、修复或目标安全。"
