"""Inventory endpoint ORM model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


class InventoryEndpoint(Base):
    __tablename__ = "inventory_endpoints"
    __table_args__ = (
        UniqueConstraint(
            "inventory_run_id",
            "method",
            "path",
            name="uq_inventory_endpoints_run_method_path",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    inventory_run_id: Mapped[int] = mapped_column(ForeignKey("inventory_runs.id"), index=True)
    method: Mapped[str] = mapped_column(String(16), index=True)
    url: Mapped[str] = mapped_column(String(500))
    path: Mapped[str] = mapped_column(String(500), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request_count: Mapped[int] = mapped_column(Integer, default=1)
    cache_control: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    response_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    set_cookie_names: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON),
        default=list,
    )
    cookie_issue_flags: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON),
        default=list,
    )
    status_codes_observed: Mapped[list[int]] = mapped_column(
        MutableList.as_mutable(JSON),
        default=list,
    )

    inventory_run = relationship("InventoryRun", back_populates="endpoints")
    parameters = relationship("InventoryParameter", back_populates="inventory_endpoint")
