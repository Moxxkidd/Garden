"""Public identity contracts and explicitly private browser state."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.scan import ScanOptions


class IdentityCollectionRequest(BaseModel):
    interaction_mode: Literal["automatic", "manual"] = "automatic"
    manual_login: "ManualLoginRequest | None" = None
    project_entry_id: int | None = Field(default=None, gt=0)
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2000)
    target_id: int = Field(gt=0)
    profile_ids: list[int] = Field(default_factory=list, max_length=10)
    include_anonymous: bool = False
    options: ScanOptions = Field(default_factory=ScanOptions)

    @model_validator(mode="after")
    def identities(self):
        if not self.profile_ids and not self.include_anonymous:
            raise ValueError("请选择至少一个身份或匿名采集。")
        if len(set(self.profile_ids)) != len(self.profile_ids) or any(
            i <= 0 for i in self.profile_ids
        ):
            raise ValueError("身份 ID 必须为不重复的正整数。")
        if self.interaction_mode == "manual":
            if (
                len(self.profile_ids) != 1
                or self.include_anonymous
                or self.options.durable is not None
            ):
                raise ValueError("人工浏览须选择一个明确身份，不使用自动队列。")
            if self.manual_login and self.manual_login.project_entry_id not in {
                None,
                self.project_entry_id,
            }:
                raise ValueError("人工登录与采集入口必须一致。")
            if self.manual_login and self.manual_login.profile_id != self.profile_ids[0]:
                raise ValueError("人工登录与采集身份必须一致。")
        elif self.manual_login is not None:
            raise ValueError("额外人工登录配置仅用于显式人工模式。")
        return self


class ManualLoginRequest(BaseModel):
    project_entry_id: int | None = Field(default=None, gt=0)
    model_config = ConfigDict(extra="forbid")
    profile_id: int = Field(gt=0)
    login_url: str = Field(min_length=1, max_length=2000)
    validate_url: str = Field(min_length=1, max_length=2000)
    success_selector: str | None = Field(default=None, max_length=500)
    success_text: str | None = Field(default=None, max_length=500)
    allowed_auth_origins: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def positive_condition(self):
        if not (self.success_selector and self.success_selector.strip()) and not (
            self.success_text and self.success_text.strip()
        ):
            raise ValueError("必须提供登录成功后的页面元素或文本，HTTP 200 不足以验证身份。")
        return self


class SessionHealthView(BaseModel):
    session_id: int | None = None
    status: Literal["ready", "checking", "expired", "validation_failed", "unknown", "revoked"]
    checked_at: datetime | None = None
    reason_code: str | None = None


class IdentityContextView(BaseModel):
    context_id: int
    context_key: str
    profile_id: int | None
    display_name: str
    health: SessionHealthView
    completeness: str


class StoredIdentityState(BaseModel):
    """Private only: never include in a public response."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    target_origin: str
    storage_state: dict = Field(default_factory=lambda: {"cookies": [], "origins": []}, repr=False)
    session_storage: dict[str, dict[str, str]] = Field(default_factory=dict, repr=False)


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_run_id: int = Field(gt=0)
    source_context_id: int = Field(gt=0)
    checkpoint_version: int = Field(gt=0)
    options: ScanOptions = Field(default_factory=ScanOptions)


class LoginAttemptView(BaseModel):
    project_entry_id: int | None = None
    profile_id: int | None = None
    id: int
    state: str
    expires_at: datetime
    session_id: int | None = None
    reason_code: str | None = None


class RecoveryPreview(BaseModel):
    source_run_id: int
    source_context_id: int
    checkpoint_version: int
    profile_id: int | None
    pending_count: int
    uncertain_count: int
    examples: list[str]
    known_scope_truncated: bool
    scope_note: str = "仅补采检查点中已知的未完成或身份不确定请求，不代表全站覆盖。"
    preview_token: str


class IdentityMatrixCell(BaseModel):
    uncertain_status_codes: list[int] = Field(default_factory=list)
    uncertain_evidence_ids: list[int] = Field(default_factory=list)
    state: Literal["observed", "identity_uncertain", "not_observed", "unknown"]
    status_codes: list[int] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[int] = Field(default_factory=list)


class IdentityMatrixRow(BaseModel):
    asset_id: str
    url: str
    method: str | None
    cells: dict[str, IdentityMatrixCell]


class IdentityMatrix(BaseModel):
    contexts: list[IdentityContextView]
    rows: list[IdentityMatrixRow]
    confirmed_subject_count: int
    auth_diagnostic_count: int
    note: str = (
        "已观察仅说明该身份下取得响应，不代表业务有效。"
        "未观察不等于不存在或无权限；覆盖不完整时为未知。"
    )


class IdentityStateImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: int = Field(gt=0)
    raw_json: str = Field(max_length=1024 * 1024, repr=False)
    verification: ManualLoginRequest


class IdentityPreviewConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_token: str = Field(min_length=20, max_length=200)


class RecoveryConfirmation(RecoveryRequest):
    preview_token: str = Field(min_length=20, max_length=200)


class IdentityPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: int = Field(gt=0)
    profile_id: int | None = Field(default=None, gt=0)
    max_requests: int | None = Field(default=None, ge=1, le=2000)
    interaction_mode: Literal["automatic", "manual"] = "automatic"


class IdentityPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(gt=0)
    total_requests: int = Field(ge=1, le=40000)
    items: list[IdentityPlanItem] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def selections(self):
        keys = [(i.entry_id, i.profile_id) for i in self.items]
        if len(set(keys)) != len(keys):
            raise ValueError("同一入口/身份不能重复。")
        if any(i.interaction_mode == "manual" and i.profile_id is None for i in self.items):
            raise ValueError("人工浏览必须绑定一个明确身份。")
        return self
