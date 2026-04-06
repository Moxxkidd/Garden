"""Finding ORM model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    check_name: Mapped[str] = mapped_column(String(120), index=True)
    dedup_key: Mapped[str] = mapped_column(String(255), index=True)
    location_key: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(120), index=True)
    severity: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)
    credential_profile_id: Mapped[int] = mapped_column(
        ForeignKey("credential_profiles.id"),
        index=True,
    )
    session_id: Mapped[int] = mapped_column(ForeignKey("auth_sessions.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("scan_jobs.id"), index=True)
    inventory_refs: Mapped[list[dict[str, object]]] = mapped_column(
        MutableList.as_mutable(JSON),
        default=list,
    )
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(
        MutableList.as_mutable(JSON),
        default=list,
    )
    trigger_explanation: Mapped[str] = mapped_column(Text)
    false_positive_boundaries: Mapped[str] = mapped_column(Text)
    reproduction_notes: Mapped[str] = mapped_column(Text)
    remediation_notes: Mapped[str] = mapped_column(Text)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    target = relationship("Target", back_populates="findings")
    credential_profile = relationship("CredentialProfile", back_populates="findings")
    auth_session = relationship("AuthSession", back_populates="findings")
    scan_job = relationship("ScanJob", back_populates="findings")
    evidences = relationship("Evidence", back_populates="finding")
    retests = relationship("FindingRetestRun", back_populates="finding")
