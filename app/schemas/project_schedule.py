"""One entry, one explicit identity, UTC fixed hours; no cron or manual replay."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.collection_queue import CollectionOptions
from app.schemas.scan import ScanOptions


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: int = Field(gt=0)
    profile_id: int | None = Field(default=None, gt=0)
    interval_hours: int = Field(default=24, ge=1, le=168)
    start_at: datetime
    options: ScanOptions = Field(default_factory=lambda: ScanOptions(durable=CollectionOptions()))

    @field_validator("start_at")
    @classmethod
    def aware_start(cls, value):
        if value.tzinfo is None:
            raise ValueError("开始时间须带时区。")
        return value

    @field_validator("options")
    @classmethod
    def durable_required(cls, value):
        if value.durable is None:
            raise ValueError("周期复查必须显式保存请求预算。")
        if value.seed_input or value.sitemap_url:
            raise ValueError("周期复查使用绑定入口，不保存额外种子或远程 sitemap。")
        return value
