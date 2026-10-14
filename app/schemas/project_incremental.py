"""Explicit project entry and identity for a bounded incremental plan."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.collection_queue import CollectionOptions


class ProjectIncrementalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(gt=0)
    entry_id: int = Field(gt=0)
    profile_id: int | None = Field(default=None, gt=0)
    important_asset_ids: list[str] = Field(default_factory=list, max_length=1000)
    stale_after_days: int = Field(default=30, ge=0, le=3650)
    mode: Literal["known_only", "continue_discovery"] = "known_only"
    options: CollectionOptions = Field(default_factory=CollectionOptions)
