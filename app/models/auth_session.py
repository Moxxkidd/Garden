"""Authenticated session ORM model."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class AuthSession(TimestampMixin, Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)
    credential_profile_id: Mapped[int] = mapped_column(
        ForeignKey("credential_profiles.id"), index=True
    )
    scan_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("scan_jobs.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    session_type: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refresh_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    session_metadata_redacted: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    storage_ref: Mapped[str] = mapped_column(String(500))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    target = relationship("Target", back_populates="auth_sessions")
    credential_profile = relationship("CredentialProfile", back_populates="auth_sessions")
    scan_job = relationship("ScanJob", back_populates="auth_sessions")
    audit_events = relationship("AuditEvent", back_populates="auth_session")
    inventory_runs = relationship("InventoryRun", back_populates="auth_session")
    scan_contexts = relationship("ScanContext", back_populates="auth_session")
    findings = relationship("Finding", back_populates="auth_session")
    evidences = relationship("Evidence", back_populates="auth_session")
