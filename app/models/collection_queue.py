"""Durable collection work. URLs containing secrets live only in protected files."""

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class CollectionQueue(TimestampMixin, Base):
    __tablename__ = "collection_queues"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    context_id: Mapped[int] = mapped_column(ForeignKey("scan_contexts.id"), unique=True)
    origin: Mapped[str] = mapped_column(String(500))
    identity_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    revision: Mapped[int] = mapped_column(default=0)
    options: Mapped[dict] = mapped_column(JSON)
    telemetry: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    truncated: Mapped[bool] = mapped_column(default=False)
    binding: Mapped[str] = mapped_column(String(64))
    private_ref: Mapped[str] = mapped_column(String(80))
    private_digest: Mapped[str] = mapped_column(String(64))
    resume_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    resume_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("collection_queues.id"), nullable=True)


class CollectionWork(TimestampMixin, Base):
    __tablename__ = "collection_work"
    __table_args__ = (UniqueConstraint("queue_id", "request_key", name="uq_collection_request"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    queue_id: Mapped[int] = mapped_column(ForeignKey("collection_queues.id"), index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(10))
    asset_type: Mapped[str] = mapped_column(String(30), default="page")
    route_key: Mapped[str] = mapped_column(String(64), index=True)
    private_ref: Mapped[str] = mapped_column(String(80))
    private_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    depth: Mapped[int] = mapped_column(default=0)
    priority: Mapped[int] = mapped_column(default=2)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(default=0)
    not_before: Mapped[float] = mapped_column(Float, default=0)


class CollectionAttempt(TimestampMixin, Base):
    __tablename__ = "collection_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    queue_id: Mapped[int] = mapped_column(ForeignKey("collection_queues.id"), index=True)
    work_id: Mapped[int | None] = mapped_column(ForeignKey("collection_work.id"), nullable=True)
    lease_token: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(30), default="reserved")
    status_code: Mapped[int | None] = mapped_column(nullable=True)
    evidence_bytes: Mapped[int] = mapped_column(default=0)
    is_retry: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    request_key: Mapped[str] = mapped_column(String(64))


class CollectionGate(Base):
    __tablename__ = "collection_gates"
    origin: Mapped[str] = mapped_column(String(500), primary_key=True)
    next_send_at: Mapped[float] = mapped_column(Float, default=0)
    cooldown_until: Mapped[float] = mapped_column(Float, default=0)
    failures: Mapped[int] = mapped_column(default=0)
