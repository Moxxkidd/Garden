"""A plan stores child references and budgets, never authentication material."""

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class IdentityPlan(TimestampMixin, Base):
    __tablename__ = "identity_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    configuration: Mapped[dict] = mapped_column(JSON)
