"""Audit trail model for login/session operations."""

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class AuditEvent(TimestampMixin, Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    target_id: Mapped[int | None] = mapped_column(
        ForeignKey("targets.id"), nullable=True, index=True
    )
    credential_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("credential_profiles.id"),
        nullable=True,
        index=True,
    )
    auth_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("auth_sessions.id"),
        nullable=True,
        index=True,
    )
    scan_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("scan_jobs.id"), nullable=True, index=True
    )
    detail_redacted: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )

    target = relationship("Target", back_populates="audit_events")
    credential_profile = relationship("CredentialProfile", back_populates="audit_events")
    auth_session = relationship("AuthSession", back_populates="audit_events")
    scan_job = relationship("ScanJob", back_populates="audit_events")
