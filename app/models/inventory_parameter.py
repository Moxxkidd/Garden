"""Inventory parameter ORM model."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class InventoryParameter(Base):
    __tablename__ = "inventory_parameters"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    inventory_run_id: Mapped[int] = mapped_column(ForeignKey("inventory_runs.id"), index=True)
    inventory_endpoint_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_endpoints.id"),
        nullable=True,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)

    inventory_run = relationship("InventoryRun", back_populates="parameters")
    inventory_endpoint = relationship("InventoryEndpoint", back_populates="parameters")
