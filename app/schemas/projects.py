"""Display-safe project requests and paginated outputs."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.assets import AssetRecord, AssetScope


class ProjectSourceRef(BaseModel):
    source: Literal["scan", "inventory"]
    run_id: int = Field(gt=0)


class ProjectEntryCreate(BaseModel):
    target_id: int = Field(gt=0)
    entry_url: str = Field(min_length=1, max_length=2000)
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True


class ProjectImportRequest(BaseModel):
    entry_id: int = Field(gt=0)
    source_ref: ProjectSourceRef


class ProjectSourceSnapshot(BaseModel):
    scope: AssetScope
    records: list[AssetRecord]
    candidates: list[AssetRecord]
    comparison_metadata: dict
    relations: list[dict] = Field(default_factory=list)
    digest: str = Field(exclude=True)


class ProjectImportPreview(BaseModel):
    token: str
    expires_in: int = 900
    subject_count: int
    observation_count: int
    candidate_count: int
    limitations: list[str] = Field(default_factory=list)


class ProjectQuery(BaseModel):
    view: Literal["assets", "observations", "candidates", "relations"] = "assets"
    site: str | None = None
    kind: str | None = None
    method: str | None = None
    identity_key: str | None = None
    project_run_id: int | None = Field(default=None, gt=0)
    asset_id: str | None = None
    original_asset_id: str | None = None
    tag: str | None = None
    q: str = Field(default="", max_length=200)
    seen_from: datetime | None = None
    seen_to: datetime | None = None
    sort: Literal["id", "url", "last_seen"] = "id"
    order: Literal["asc", "desc"] = "asc"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class ProjectPage(BaseModel):
    items: list[dict]
    matched: int
    counts: dict[str, int]
    limitations: list[str]
    revision: int


class ProjectAssetDetail(BaseModel):
    asset: dict
    history: ProjectPage
    relations: list[dict]
    annotation: dict
    aliases: list[str]
    corrections: list[dict]


class ProjectCorrectionRequest(BaseModel):
    action: Literal["merge", "split"]
    asset_ids: list[str] = Field(min_length=1, max_length=100)
    target_asset_id: str | None = None
    observation_ids: list[int] = Field(default_factory=list, max_length=10000)
    reason: str = Field(min_length=1, max_length=2000)


class ProjectCorrectionPreview(BaseModel):
    token: str
    expires_in: int = 900
    before_subject_count: int
    after_subject_count: int
    affected_observation_count: int
    limitations: list[str] = Field(default_factory=list)


class ProjectComparison(BaseModel):
    before_run_id: int
    after_run_id: int
    items: list[dict]
    counts: dict[str, int]
    limitations: list[str]


class ProjectView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str
    status: str
    revision: int


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class ProjectRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


class ProjectEntryUpdate(ProjectEntryCreate):
    expected_revision: int = Field(ge=0)


class ProjectConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_token: str = Field(min_length=1, max_length=200)


class ProjectAnnotationRequest(ProjectRevision):
    asset_id: str = Field(min_length=1, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=2000)


class ProjectRevokeRequest(ProjectRevision):
    reason: str = Field(min_length=1, max_length=2000)
