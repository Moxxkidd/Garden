"""Configuration and controlled artifact reference for a local export job."""

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class DeliveryExport(TimestampMixin, Base):
    __tablename__ = "delivery_exports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    revision: Mapped[int]
    configuration: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="generating", index=True)
    error_code: Mapped[str | None] = mapped_column(String(80))
    artifact: Mapped[str | None] = mapped_column(String(100))
