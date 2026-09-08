"""统一 quick 与认证覆盖验证的传输协议。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import AssessmentMode, ContextKind, ReplayVerdict
from app.schemas.scan import ScanOptions, ScanRunView


class AssessmentStartRequest(BaseModel):
    url: str = Field(min_length=1, max_length=1000)
    mode: AssessmentMode = AssessmentMode.QUICK
    target_id: int | None = Field(default=None, gt=0)
    source_run_id: int | None = Field(default=None, gt=0)
    user_profile_id: int | None = Field(default=None, gt=0)
    admin_profile_id: int | None = Field(default=None, gt=0)
    active_checks_enabled: bool = False
    authorization_confirmed: bool = False
    authorization_confirmed_by: str | None = Field(default=None, max_length=255)
    options: ScanOptions = Field(default_factory=ScanOptions)

    @model_validator(mode="after")
    def validate_mode_and_authorization(self) -> AssessmentStartRequest:
        profile_ids = (self.user_profile_id, self.admin_profile_id)
        if self.mode == AssessmentMode.QUICK and any(item is not None for item in profile_ids):
            raise ValueError("quick 模式不接受认证凭证档案。")
        if self.mode == AssessmentMode.AUTHENTICATED_COVERAGE and any(
            item is None for item in profile_ids
        ):
            raise ValueError("authenticated_coverage 模式必须同时提供 user/admin 凭证档案。")
        if self.active_checks_enabled and not self.authorization_confirmed:
            raise ValueError("启用主动重放前必须显式确认授权。")
        return self


class PassiveCoverageStartRequest(BaseModel):
    """Public passive-only coverage request used by guided and HTTP adapters."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=1000)
    target_id: int | None = Field(default=None, gt=0)
    source_run_id: int | None = Field(default=None, gt=0)
    user_profile_id: int = Field(gt=0)
    admin_profile_id: int = Field(gt=0)
    options: ScanOptions = Field(default_factory=ScanOptions)

    @property
    def active_checks_enabled(self) -> bool:
        return False

    def to_assessment_request(self) -> AssessmentStartRequest:
        return AssessmentStartRequest(
            url=self.url,
            mode=AssessmentMode.AUTHENTICATED_COVERAGE,
            target_id=self.target_id,
            source_run_id=self.source_run_id,
            user_profile_id=self.user_profile_id,
            admin_profile_id=self.admin_profile_id,
            active_checks_enabled=False,
            authorization_confirmed=False,
            options=self.options,
        )


class CoverageDifferenceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    identity_key: str
    classification: str
    anonymous_state: str = "unknown"
    user_state: str = "unknown"
    admin_state: str = "unknown"
    anonymous_present: bool | None = None
    user_present: bool | None = None
    admin_present: bool | None = None
    context_summaries: dict[str, object] = Field(default_factory=dict)
    confidence: str = "low"
    diagnostic: str | None = None


class ReplayExecutionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_request_id: int
    source_context_id: int
    target_context_id: int
    status: str
    verdict: ReplayVerdict | None = None
    verdict_reason: str | None = None
    response_status_code: int | None = None
    response_final_url_redacted: str | None = None
    response_fingerprint: str | None = None
    response_summary_redacted: dict[str, object] = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AssessmentRunView(ScanRunView):
    model_config = ConfigDict(from_attributes=True)

    completeness: str
    active_checks_enabled: bool
    authorization_confirmed_at: datetime | None = None
    authorization_confirmed_by: str | None = None
    context_counts: dict[str, int] = Field(
        default_factory=lambda: {"anonymous": 0, "user": 0, "admin": 0}
    )
    difference_count: int = Field(default=0, ge=0)
    replay_count: int = Field(default=0, ge=0)

    @field_validator("context_counts")
    @classmethod
    def validate_context_counts(cls, value: dict[str, int]) -> dict[str, int]:
        allowed_keys = {item.value for item in ContextKind}
        unknown_keys = set(value) - allowed_keys
        if unknown_keys:
            raise ValueError("context_counts 只能包含 anonymous、user、admin。")
        if any(count < 0 for count in value.values()):
            raise ValueError("context_counts 不能包含负数。")
        return value
