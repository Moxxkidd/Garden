"""可审计且不直接保存敏感精确值的捕获请求。"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ScanRequest(TimestampMixin, Base):
    __tablename__ = "scan_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    source_context_id: Mapped[int] = mapped_column(ForeignKey("scan_contexts.id"), index=True)
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("scan_assets.id"), nullable=True, index=True
    )
    method: Mapped[str] = mapped_column(String(16), index=True)
    normalized_url: Mapped[str] = mapped_column(String(1000))
    redacted_url: Mapped[str] = mapped_column(String(1000))
    header_names: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JSON), default=list)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    replay_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    replay_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    replay_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    protected_storage_ref: Mapped[str] = mapped_column(String(1000))

    scan_run = relationship("ScanRun", back_populates="requests")
    source_context = relationship(
        "ScanContext",
        back_populates="requests",
        foreign_keys=[source_context_id],
    )
    asset = relationship("ScanAsset", back_populates="requests")
    replay_executions = relationship("ReplayExecution", back_populates="source_request")
