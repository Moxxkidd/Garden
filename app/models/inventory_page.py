"""Inventory page ORM model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class InventoryPage(Base):
    __tablename__ = "inventory_pages"
    __table_args__ = (
        UniqueConstraint("inventory_run_id", "url", name="uq_inventory_pages_run_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    inventory_run_id: Mapped[int] = mapped_column(ForeignKey("inventory_runs.id"), index=True)
    url: Mapped[str] = mapped_column(String(500))
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_control: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    first_visited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_visited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    visit_count: Mapped[int] = mapped_column(Integer, default=1)

    inventory_run = relationship("InventoryRun", back_populates="pages")
