"""Persistent route subjects and immutable, source-attributed observations."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ProjectAsset(TimestampMixin, Base):
    __tablename__ = "project_assets"
    __table_args__ = (UniqueConstraint("project_id", "natural_key", name="uq_project_asset_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid4().hex)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    natural_key: Mapped[str] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(String(40), default="project-route-v1")
    site: Mapped[str | None] = mapped_column(String(500), index=True)
    method: Mapped[str | None] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    original_type: Mapped[str] = mapped_column(String(40))
    url: Mapped[str] = mapped_column(Text)
    manual: Mapped[bool] = mapped_column(default=False)


class ProjectObservation(TimestampMixin, Base):
    __tablename__ = "project_observations"
    __table_args__ = (UniqueConstraint("project_id", "source_key", name="uq_project_observation"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    project_run_id: Mapped[int] = mapped_column(ForeignKey("project_runs.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("project_assets.id"), index=True)
    display_asset_id: Mapped[str] = mapped_column(ForeignKey("project_assets.id"), index=True)
    source_key: Mapped[str] = mapped_column(String(200))
    identity_key: Mapped[str] = mapped_column(Text, index=True)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record: Mapped[dict] = mapped_column(JSON)
    search_text: Mapped[str] = mapped_column(Text, default="")


class ProjectCandidate(TimestampMixin, Base):
    __tablename__ = "project_candidates"
    __table_args__ = (UniqueConstraint("project_id", "source_key", name="uq_project_candidate"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    project_run_id: Mapped[int] = mapped_column(ForeignKey("project_runs.id"), index=True)
    source_key: Mapped[str] = mapped_column(String(200))
    natural_key: Mapped[str] = mapped_column(Text)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("project_assets.id"), index=True)
    record: Mapped[dict] = mapped_column(JSON)
