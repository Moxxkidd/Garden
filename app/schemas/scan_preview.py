"""Safe, non-authoritative presentation of a proposed scan."""

from pydantic import BaseModel, Field

from app.schemas.scan import ScanOptions


class PreviewIssue(BaseModel):
    code: str
    field: str
    severity: str = "error"
    message: str


class PreviewContext(BaseModel):
    kind: str
    profile_id: int | None = None
    profile_name: str | None = None


class ScanPreview(BaseModel):
    mode: str
    entry_display: str
    origin_display: str
    contexts: list[PreviewContext]
    effective_options: ScanOptions
    issues: list[PreviewIssue] = Field(default_factory=list)
    can_submit: bool
    budget_note: str
    configuration_fingerprint: str
