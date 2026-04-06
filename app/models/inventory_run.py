"""Inventory run ORM model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base
from app.models.mixins import TimestampMixin


class InventoryRun(TimestampMixin, Base):
    __tablename__ = "inventory_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)
    credential_profile_id: Mapped[int] = mapped_column(
        ForeignKey("credential_profiles.id"),
        index=True,
    )
    auth_session_id: Mapped[int] = mapped_column(ForeignKey("auth_sessions.id"), index=True)
    scan_job_id: Mapped[int] = mapped_column(ForeignKey("scan_jobs.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_from_url: Mapped[str] = mapped_column(String(500))
    max_pages: Mapped[int] = mapped_column(Integer, default=25)
    max_depth: Mapped[int] = mapped_column(Integer, default=2)
    max_requests: Mapped[int] = mapped_column(Integer, default=100)
    delay_ms: Mapped[int] = mapped_column(Integer, default=250)
    include_rules: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JSON), default=list)
    exclude_rules: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JSON), default=list)
    pages_count: Mapped[int] = mapped_column(Integer, default=0)
    endpoints_count: Mapped[int] = mapped_column(Integer, default=0)
    parameters_count: Mapped[int] = mapped_column(Integer, default=0)
    annotations_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    target = relationship("Target", back_populates="inventory_runs")
    credential_profile = relationship("CredentialProfile", back_populates="inventory_runs")
    auth_session = relationship("AuthSession", back_populates="inventory_runs")
    scan_job = relationship("ScanJob", back_populates="inventory_runs")
    pages = relationship(
        "InventoryPage",
        back_populates="inventory_run",
        cascade="all, delete-orphan",
    )
    endpoints = relationship(
        "InventoryEndpoint",
        back_populates="inventory_run",
        cascade="all, delete-orphan",
    )
    parameters = relationship(
        "InventoryParameter",
        back_populates="inventory_run",
        cascade="all, delete-orphan",
    )
    annotations = relationship(
        "InventoryAnnotation",
        back_populates="inventory_run",
        cascade="all, delete-orphan",
    )
    evidences = relationship("Evidence", back_populates="inventory_run")
