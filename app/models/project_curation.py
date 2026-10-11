"""Evidence-backed relationships and reversible editorial records."""

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ProjectRelation(TimestampMixin, Base):
    __tablename__ = "project_relations"
    __table_args__ = (UniqueConstraint("project_run_id", "edge_key", name="uq_project_relation"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    project_run_id: Mapped[int] = mapped_column(ForeignKey("project_runs.id"), index=True)
    edge_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(30), index=True)
    from_asset_id: Mapped[str | None] = mapped_column(ForeignKey("project_assets.id"))
    to_asset_id: Mapped[str | None] = mapped_column(ForeignKey("project_assets.id"))
    detail: Mapped[dict] = mapped_column(JSON)


class ProjectAnnotation(TimestampMixin, Base):
    __tablename__ = "project_annotations"
    asset_id: Mapped[str] = mapped_column(ForeignKey("project_assets.id"), primary_key=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")


class ProjectCorrection(TimestampMixin, Base):
    __tablename__ = "project_corrections"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    action: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="active")
    target_asset_id: Mapped[str] = mapped_column(ForeignKey("project_assets.id"))
    source_asset_ids: Mapped[list] = mapped_column(JSON)
    observation_ids: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)


class ProjectCorrectionMember(Base):
    __tablename__ = "project_correction_members"
    correction_id: Mapped[int] = mapped_column(
        ForeignKey("project_corrections.id"), primary_key=True
    )
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("project_observations.id"), primary_key=True
    )
    previous_asset_id: Mapped[str] = mapped_column(ForeignKey("project_assets.id"))
    next_asset_id: Mapped[str] = mapped_column(ForeignKey("project_assets.id"))


class ProjectAudit(TimestampMixin, Base):
    __tablename__ = "project_audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(120), default="local_operator")
    detail: Mapped[dict] = mapped_column(JSON)
