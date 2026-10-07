"""Durable manual-login lifecycle, without browser secrets."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class LoginAttempt(TimestampMixin, Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("credential_profiles.id"))
    state: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("auth_sessions.id"), nullable=True)
    owner_token: Mapped[str] = mapped_column(String(64))
    reason_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    configuration: Mapped[dict] = mapped_column(JSON)
