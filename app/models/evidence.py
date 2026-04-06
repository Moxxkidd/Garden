"""Evidence ORM model."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)
    credential_profile_id: Mapped[int] = mapped_column(
        ForeignKey("credential_profiles.id"),
        index=True,
    )
    session_id: Mapped[int] = mapped_column(ForeignKey("auth_sessions.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("scan_jobs.id"), index=True)
    inventory_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_runs.id"),
        nullable=True,
        index=True,
    )
    finding_id: Mapped[int | None] = mapped_column(
        ForeignKey("findings.id"),
        nullable=True,
        index=True,
    )
    evidence_type: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    summary: Mapped[str] = mapped_column(Text)
    storage_ref: Mapped[str] = mapped_column(String(500))
    preview_redacted: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    http_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    target = relationship("Target", back_populates="evidences")
    credential_profile = relationship("CredentialProfile", back_populates="evidences")
    auth_session = relationship("AuthSession", back_populates="evidences")
    scan_job = relationship("ScanJob", back_populates="evidences")
    inventory_run = relationship("InventoryRun", back_populates="evidences")
    finding = relationship("Finding", back_populates="evidences")
