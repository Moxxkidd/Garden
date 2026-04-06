"""Scan job input schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import JobStatus


class ScanJobCreate(BaseModel):
    target_id: int = Field(gt=0)
    credential_profile_id: int = Field(gt=0)
    status: JobStatus = JobStatus.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: str | None = None
    error_message: str | None = None
