"""每次统一验证运行中的身份上下文。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ScanContext(TimestampMixin, Base):
    __tablename__ = "scan_contexts"
    __table_args__ = (UniqueConstraint("scan_run_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    credential_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("credential_profiles.id"), nullable=True, index=True
    )
    auth_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("auth_sessions.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    login_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    session_validation_status: Mapped[str] = mapped_column(
        String(40), default="pending", index=True
    )
    collection_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    completeness: Mapped[str] = mapped_column(String(64), default="pending", index=True)
    asset_count: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    scan_run = relationship("ScanRun", back_populates="contexts")
    credential_profile = relationship("CredentialProfile")
    auth_session = relationship("AuthSession", back_populates="scan_contexts")
    assets = relationship("ScanAsset", back_populates="context")
    requests = relationship(
        "ScanRequest",
        back_populates="source_context",
        foreign_keys="ScanRequest.source_context_id",
    )
    source_replay_executions = relationship(
        "ReplayExecution",
        back_populates="source_context",
        foreign_keys="ReplayExecution.source_context_id",
    )
    target_replay_executions = relationship(
        "ReplayExecution",
        back_populates="target_context",
        foreign_keys="ReplayExecution.target_context_id",
    )
