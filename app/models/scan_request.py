"""可审计且不直接保存敏感精确值的捕获请求。"""

from __future__ import annotations

from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ScanRequest(TimestampMixin, Base):
    __tablename__ = "scan_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scan_run_id", "source_context_id"],
            ["scan_contexts.scan_run_id", "scan_contexts.id"],
            name="fk_scan_requests_run_source_context",
        ),
        ForeignKeyConstraint(
            ["scan_run_id", "asset_id"],
            ["scan_assets.scan_run_id", "scan_assets.id"],
            name="fk_scan_requests_run_asset",
        ),
        UniqueConstraint(
            "scan_run_id",
            "id",
            "source_context_id",
            name="uq_scan_requests_run_id_source_context",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    source_context_id: Mapped[int] = mapped_column(index=True)
    asset_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(16), index=True)
    normalized_redacted_url: Mapped[str] = mapped_column(String(1000))
    header_names: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JSON), default=list)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    replay_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    replay_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    replay_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    protected_storage_ref: Mapped[str] = mapped_column(String(1000))

    @classmethod
    def from_capture(
        cls,
        *,
        scan_run_id: int,
        source_context_id: int,
        method: str,
        raw_url: str,
        header_names: list[str],
        fingerprint: str,
        protected_storage_ref: str,
        asset_id: int | None = None,
    ) -> ScanRequest:
        return cls(
            scan_run_id=scan_run_id,
            source_context_id=source_context_id,
            asset_id=asset_id,
            method=method.upper(),
            normalized_redacted_url=_normalize_and_redact_url(raw_url),
            header_names=sorted(set(header_names)),
            fingerprint=fingerprint,
            protected_storage_ref=protected_storage_ref,
        )

    @validates("normalized_redacted_url")
    def validate_normalized_redacted_url(self, _key: str, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("normalized_redacted_url 必须脱敏。")
        if any(
            query_value not in {"", "[REDACTED]"}
            for _name, query_value in parse_qsl(parsed.query, keep_blank_values=True)
        ):
            raise ValueError("normalized_redacted_url 必须脱敏。")
        return value

    scan_run = relationship("ScanRun", back_populates="requests")
    source_context = relationship(
        "ScanContext",
        back_populates="requests",
        foreign_keys=[scan_run_id, source_context_id],
        viewonly=True,
    )
    asset = relationship(
        "ScanAsset",
        back_populates="requests",
        foreign_keys=[scan_run_id, asset_id],
        viewonly=True,
    )
    replay_executions = relationship(
        "ReplayExecution",
        back_populates="source_request",
        foreign_keys=(
            "[ReplayExecution.scan_run_id, ReplayExecution.source_request_id, "
            "ReplayExecution.source_context_id]"
        ),
        viewonly=True,
    )


def _normalize_and_redact_url(raw_url: str) -> str:
    parsed = urlsplit(raw_url)
    host = (parsed.hostname or "").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parsed.path or "/"
    query_names = sorted(name for name, _value in parse_qsl(parsed.query, keep_blank_values=True))
    query = "&".join(f"{quote(name, safe='')}=[REDACTED]" for name in query_names)
    return urlunsplit((parsed.scheme.lower(), host, path, query, ""))
