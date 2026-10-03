"""Public identity contracts and explicitly private browser state."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.scan import ScanOptions


class IdentityCollectionRequest(BaseModel):
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
        return self


class ManualLoginRequest(BaseModel):
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
    id: int
    state: str
    expires_at: datetime
    session_id: int | None = None
    reason_code: str | None = None
