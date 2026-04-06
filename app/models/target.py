"""Target ORM model."""

from sqlalchemy import JSON, String
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class Target(TimestampMixin, Base):
    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    base_url: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    owner: Mapped[str] = mapped_column(String(120), index=True)
    tags: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JSON), default=list)
    status: Mapped[str] = mapped_column(String(32), index=True)

    credential_profiles = relationship("CredentialProfile", back_populates="target")
    scan_jobs = relationship("ScanJob", back_populates="target")
    auth_sessions = relationship("AuthSession", back_populates="target")
    audit_events = relationship("AuditEvent", back_populates="target")
    inventory_runs = relationship("InventoryRun", back_populates="target")
    findings = relationship("Finding", back_populates="target")
    evidences = relationship("Evidence", back_populates="target")
