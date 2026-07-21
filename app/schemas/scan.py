"""Interface-neutral contracts for the URL scan application service."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ScanRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"


class ScanStageName(str, Enum):
    VALIDATE = "validate"
    DISCOVER = "discover"
    COLLECT = "collect"
    NORMALIZE = "normalize"
    ANALYZE = "analyze"
    REPORT = "report"


class ScanOptions(BaseModel):
    """Bounded user controls; infrastructure defaults are injected by the service."""

    max_pages: int = Field(default=10, ge=1, le=100)
    max_depth: int = Field(default=2, ge=0, le=5)
    request_timeout_seconds: float | None = Field(default=None, gt=0, le=60)
    overall_timeout_seconds: float | None = Field(default=None, gt=0, le=1800)
    retry_attempts: int | None = Field(default=None, ge=0, le=2)
    max_redirects: int = Field(default=5, ge=0, le=10)
    user_agent: str = Field(default="Garden-Authorized-Asset-Scanner/0.2", max_length=200)


class ScanStartRequest(BaseModel):
    url: str = Field(min_length=1, max_length=1000)
    options: ScanOptions = Field(default_factory=ScanOptions)


class ScanStageView(BaseModel):
    name: str
    position: int
    status: str
    attempt: int
    summary: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ScanFailureView(BaseModel):
    stage: str
    code: str
    message: str
    url: str | None = None
    retryable: bool
    attempt: int
    occurred_at: datetime


class ScanRunView(BaseModel):
    id: int
    input_url: str
    normalized_url: str
    status: str
    current_stage: str
    progress: int
    retry_count: int
    report_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    report_generated_at: datetime | None = None
    created_at: datetime
    stages: list[ScanStageView] = Field(default_factory=list)
    failures: list[ScanFailureView] = Field(default_factory=list)
    asset_count: int = 0
    evidence_count: int = 0
    finding_count: int = 0


class FetchResult(BaseModel):
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    content_type: str | None = None
    body_text: str = ""
    title: str | None = None
    discovered_urls: list[str] = Field(default_factory=list)
    redirects: list[str] = Field(default_factory=list)
    attempts: int = 1
    elapsed_ms: int = 0


class AnalysisCandidate(BaseModel):
    dedup_key: str
    title: str
    category: str
    severity: str
    confidence: str
    summary: str
    remediation: str
    asset_ids: list[int] = Field(default_factory=list)
    evidence_ids: list[int] = Field(default_factory=list)
