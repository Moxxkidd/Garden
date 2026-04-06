"""Inventory annotation ORM model."""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class InventoryAnnotation(Base):
    __tablename__ = "inventory_annotations"
    __table_args__ = (
        UniqueConstraint(
            "inventory_run_id",
            "subject_type",
            "subject_ref",
            "marker",
            name="uq_inventory_annotations_subject_marker",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    inventory_run_id: Mapped[int] = mapped_column(ForeignKey("inventory_runs.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(32), index=True)
    subject_ref: Mapped[str] = mapped_column(String(500), index=True)
    marker: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(String(255))

    inventory_run = relationship("InventoryRun", back_populates="annotations")
