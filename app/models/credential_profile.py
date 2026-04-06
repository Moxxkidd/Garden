"""Credential profile ORM model."""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class CredentialProfile(TimestampMixin, Base):
    __tablename__ = "credential_profiles"
    __table_args__ = (
        UniqueConstraint("target_id", "name", name="uq_credential_profiles_target_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    role: Mapped[str] = mapped_column(String(120), index=True)
    auth_type: Mapped[str] = mapped_column(String(32), index=True)
    username: Mapped[str] = mapped_column(String(255), index=True)
    secret_ref: Mapped[str] = mapped_column(String(255))
    login_config_path: Mapped[str] = mapped_column(String(500))

    target = relationship("Target", back_populates="credential_profiles")
    scan_jobs = relationship("ScanJob", back_populates="credential_profile")
    auth_sessions = relationship("AuthSession", back_populates="credential_profile")
    audit_events = relationship("AuditEvent", back_populates="credential_profile")
    inventory_runs = relationship("InventoryRun", back_populates="credential_profile")
    findings = relationship("Finding", back_populates="credential_profile")
    evidences = relationship("Evidence", back_populates="credential_profile")
