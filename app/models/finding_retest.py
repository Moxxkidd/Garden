"""Finding retest ORM model."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FindingRetestRun(Base):
    __tablename__ = "finding_retests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)
    credential_profile_id: Mapped[int] = mapped_column(
        ForeignKey("credential_profiles.id"),
        index=True,
    )
    session_id: Mapped[int | None] = mapped_column(ForeignKey("auth_sessions.id"), nullable=True)
    inventory_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_runs.id"),
        nullable=True,
        index=True,
    )
    scan_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("scan_jobs.id"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    finding = relationship("Finding", back_populates="retests")
    target = relationship("Target")
    credential_profile = relationship("CredentialProfile")
    auth_session = relationship("AuthSession")
    inventory_run = relationship("InventoryRun")
    scan_job = relationship("ScanJob")
