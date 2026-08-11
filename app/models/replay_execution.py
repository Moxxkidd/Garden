"""受策略约束的跨上下文授权重放执行记录。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ReplayExecution(TimestampMixin, Base):
    __tablename__ = "replay_executions"
    __table_args__ = (
        UniqueConstraint(
            "scan_run_id",
            "source_request_id",
            "target_context_id",
            "policy_hash",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    source_request_id: Mapped[int] = mapped_column(ForeignKey("scan_requests.id"), index=True)
    source_context_id: Mapped[int] = mapped_column(ForeignKey("scan_contexts.id"), index=True)
    target_context_id: Mapped[int] = mapped_column(ForeignKey("scan_contexts.id"), index=True)
    policy_snapshot: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )
    policy_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redirects: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JSON), default=list)
    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_final_url_redacted: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    response_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response_summary_redacted: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )
    verdict: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    verdict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    scan_run = relationship("ScanRun", back_populates="replay_executions")
    source_request = relationship("ScanRequest", back_populates="replay_executions")
    source_context = relationship(
        "ScanContext",
        back_populates="source_replay_executions",
        foreign_keys=[source_context_id],
    )
    target_context = relationship(
        "ScanContext",
        back_populates="target_replay_executions",
        foreign_keys=[target_context_id],
    )
