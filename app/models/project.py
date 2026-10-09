"""Project ownership and immutable source-run membership."""

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=0)


class ProjectEntry(TimestampMixin, Base):
    __tablename__ = "project_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"))
    name: Mapped[str] = mapped_column(String(120))
    entry_url: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(500))
    target_base_url: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)


class ProjectRun(TimestampMixin, Base):
    __tablename__ = "project_runs"
    __table_args__ = (UniqueConstraint("project_id", "source", "run_id", name="uq_project_run"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("project_entries.id"))
    source: Mapped[str] = mapped_column(String(20))
    run_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(80))
    source_digest: Mapped[str | None] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
