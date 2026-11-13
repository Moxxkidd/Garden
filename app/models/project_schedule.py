"""Explicit local cadence; actual execution remains a ScanRun."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ProjectSchedule(TimestampMixin, Base):
    __tablename__ = "project_schedules"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("project_entries.id"))
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("credential_profiles.id"))
    configuration: Mapped[dict] = mapped_column(JSON)
    binding: Mapped[str] = mapped_column(String(64))
    interval_hours: Mapped[int]
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_result: Mapped[str | None] = mapped_column(String(60))
    last_run_id: Mapped[int | None] = mapped_column(ForeignKey("scan_runs.id"))
