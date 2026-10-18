"""Explicit bounded selection and fresh budgets for a linked collection run."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.collection_queue import CollectionOptions


class CollectionResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queue_id: int = Field(gt=0)
    work_ids: list[int] | None = Field(default=None, max_length=10000)
    mode: Literal["known_only", "continue_discovery"] = "known_only"
    options: CollectionOptions = Field(default_factory=CollectionOptions)
