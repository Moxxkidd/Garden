"""Bounded, optional controls for durable collection."""

from pydantic import BaseModel, ConfigDict, Field


class CollectionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_requests: int = Field(default=1000, ge=1, le=100000)
    max_work: int = Field(default=2000, ge=1, le=10000)
    max_evidence_bytes: int = Field(default=32 * 1024 * 1024, ge=1, le=256 * 1024 * 1024)
    max_variants: int = Field(default=20, ge=1, le=200)
    origin_concurrency: int = Field(default=1, ge=1, le=4)
    min_interval_ms: int = Field(default=250, ge=0, le=60000)
    retry_attempts: int = Field(default=1, ge=0, le=2)
