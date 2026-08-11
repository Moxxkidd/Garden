"""匿名、用户与管理员上下文之间的被动覆盖差异。"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class CoverageDifference(TimestampMixin, Base):
    __tablename__ = "coverage_differences"
    __table_args__ = (UniqueConstraint("scan_run_id", "identity_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    identity_key: Mapped[str] = mapped_column(String(1000), index=True)
    classification: Mapped[str] = mapped_column(String(64), index=True)
    anonymous_state: Mapped[str] = mapped_column(String(32), default="unknown")
    user_state: Mapped[str] = mapped_column(String(32), default="unknown")
    admin_state: Mapped[str] = mapped_column(String(32), default="unknown")
    anonymous_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    user_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    admin_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    context_summaries: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )
    confidence: Mapped[str] = mapped_column(String(32), default="low", index=True)
    diagnostic: Mapped[str | None] = mapped_column(Text, nullable=True)

    scan_run = relationship("ScanRun", back_populates="coverage_differences")
