"""Scan job ORM model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ScanJob(TimestampMixin, Base):
    __tablename__ = "scan_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)
    credential_profile_id: Mapped[int] = mapped_column(
        ForeignKey("credential_profiles.id"),
        index=True,
    )
    status: Mapped[str] = mapped_column(index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    target = relationship("Target", back_populates="scan_jobs")
    credential_profile = relationship("CredentialProfile", back_populates="scan_jobs")
    auth_sessions = relationship("AuthSession", back_populates="scan_job")
    audit_events = relationship("AuditEvent", back_populates="scan_job")
    inventory_runs = relationship("InventoryRun", back_populates="scan_job")
    findings = relationship("Finding", back_populates="scan_job")
    evidences = relationship("Evidence", back_populates="scan_job")
