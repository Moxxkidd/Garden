"""Interface-neutral contracts for the URL scan application service."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AssessmentMode, CompletenessStatus, ContextKind


class ScanRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    INTERRUPTED = "interrupted"


class ScanStageName(str, Enum):
    VALIDATE = "validate"
    ESTABLISH_CONTEXTS = "establish_contexts"
    COLLECT = "collect"
    NORMALIZE = "normalize"
    COMPARE_COVERAGE = "compare_coverage"
    REPLAY_AUTHORIZATION = "replay_authorization"
    ANALYZE = "analyze"
    REPORT = "report"


class ScanOptions(BaseModel):
    """Bounded user controls; infrastructure defaults are injected by the service."""

    max_pages: int = Field(default=50, ge=1, le=500)
    max_resources: int = Field(default=200, ge=0, le=2000)
    max_depth: int = Field(default=2, ge=0, le=5)
    request_timeout_seconds: float | None = Field(default=None, gt=0, le=60)
    overall_timeout_seconds: float | None = Field(default=None, gt=0, le=1800)
    retry_attempts: int | None = Field(default=None, ge=0, le=2)
    max_redirects: int = Field(default=5, ge=0, le=10)
    user_agent: str = Field(default="Garden-Authorized-Asset-Scanner/0.2", max_length=200)


class ScanStartRequest(BaseModel):
    url: str = Field(min_length=1, max_length=1000)
    options: ScanOptions = Field(default_factory=ScanOptions)


class DiscoveredAsset(BaseModel):
    """A same-document discovery with an initial resource classification."""

    url: str
    asset_type: str


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


class ScanContextView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: ContextKind
    credential_profile_id: int | None = None
    auth_session_id: int | None = None
    status: str
    login_status: str = "pending"
    session_validation_status: str = "pending"
    collection_status: str = "pending"
    completeness: str = CompletenessStatus.PENDING.value
    asset_count: int = 0
    request_count: int = 0
    failure_count: int = 0
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ScanRunView(BaseModel):
    id: int
    mode: AssessmentMode = AssessmentMode.QUICK
    target_id: int | None = None
    source_run_id: int | None = None
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
    contexts: list[ScanContextView] = Field(default_factory=list)
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
    body_size_bytes: int = 0
    body_is_text: bool = True
    title: str | None = None
    discovered_urls: list[str] = Field(default_factory=list)
    discovered_assets: list[DiscoveredAsset] = Field(default_factory=list)
    body_sha256: str = ""
    body_truncated: bool = False
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
