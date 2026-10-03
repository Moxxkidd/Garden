"""Bounded private pending-work checkpoint reference."""

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class IdentityCheckpoint(TimestampMixin, Base):
    __tablename__ = "identity_checkpoints"
    __table_args__ = (
        UniqueConstraint("context_id", "version", name="uq_identity_checkpoint_version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey("scan_contexts.id"))
    version: Mapped[int] = mapped_column(Integer)
    storage_ref: Mapped[str] = mapped_column(Text)
    configuration_digest: Mapped[str] = mapped_column(String(64))
    pending_count: Mapped[int] = mapped_column(Integer)
    uncertain_count: Mapped[int] = mapped_column(Integer)
