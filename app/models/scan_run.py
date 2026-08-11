"""Persistent parent run and normalized URL-scan records."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ScanRun(TimestampMixin, Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(40), default="quick", index=True)
    target_id: Mapped[int | None] = mapped_column(
        ForeignKey("targets.id"), nullable=True, index=True
    )
    source_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scan_runs.id"), nullable=True, index=True
    )
    input_url: Mapped[str] = mapped_column(String(1000))
    normalized_url: Mapped[str] = mapped_column(String(1000), index=True)
    active_key: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(40), index=True, default="queued")
    current_stage: Mapped[str] = mapped_column(String(40), index=True, default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    options: Mapped[dict[str, object]] = mapped_column(MutableDict.as_mutable(JSON), default=dict)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    active_checks_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    authorization_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    authorization_confirmed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    completeness: Mapped[str] = mapped_column(String(64), default="pending", index=True)
    report_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    report_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    target = relationship("Target")
    source_run = relationship(
        "ScanRun",
        back_populates="derived_runs",
        foreign_keys=[source_run_id],
        remote_side=[id],
    )
    derived_runs = relationship(
        "ScanRun",
        back_populates="source_run",
        foreign_keys=[source_run_id],
    )
    stages = relationship("ScanRunStage", back_populates="scan_run", cascade="all, delete-orphan")
    contexts = relationship(
        "ScanContext",
        back_populates="scan_run",
        cascade="all, delete-orphan",
        order_by="ScanContext.id",
    )
    assets = relationship("ScanAsset", back_populates="scan_run", cascade="all, delete-orphan")
    requests = relationship("ScanRequest", back_populates="scan_run", cascade="all, delete-orphan")
    coverage_differences = relationship(
        "CoverageDifference", back_populates="scan_run", cascade="all, delete-orphan"
    )
    replay_executions = relationship(
        "ReplayExecution", back_populates="scan_run", cascade="all, delete-orphan"
    )
    evidence = relationship("ScanEvidence", back_populates="scan_run", cascade="all, delete-orphan")
    findings = relationship("ScanFinding", back_populates="scan_run", cascade="all, delete-orphan")
    failures = relationship("ScanFailure", back_populates="scan_run", cascade="all, delete-orphan")


class ScanRunStage(Base):
    __tablename__ = "scan_run_stages"
    __table_args__ = (UniqueConstraint("scan_run_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(40), index=True)
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    scan_run = relationship("ScanRun", back_populates="stages")


class ScanAsset(Base):
    __tablename__ = "scan_assets"
    __table_args__ = (
        UniqueConstraint(
            "scan_run_id",
            "context_id",
            "asset_type",
            "url",
            name="uq_scan_assets_run_context_type_url",
        ),
        UniqueConstraint("scan_run_id", "id", name="uq_scan_assets_run_id_id"),
        ForeignKeyConstraint(
            ["scan_run_id", "context_id"],
            ["scan_contexts.scan_run_id", "scan_contexts.id"],
            name="fk_scan_assets_run_context",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    context_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    identity_key: Mapped[str | None] = mapped_column(String(1000), nullable=True, index=True)
    asset_type: Mapped[str] = mapped_column(String(40), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    attributes: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    scan_run = relationship("ScanRun", back_populates="assets")
    context = relationship(
        "ScanContext",
        back_populates="assets",
        foreign_keys=[scan_run_id, context_id],
        viewonly=True,
    )
    requests = relationship(
        "ScanRequest",
        back_populates="asset",
        foreign_keys="[ScanRequest.scan_run_id, ScanRequest.asset_id]",
        viewonly=True,
    )


class ScanEvidence(Base):
    __tablename__ = "scan_evidence"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("scan_assets.id"), nullable=True, index=True
    )
    evidence_type: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(500))
    source_url: Mapped[str] = mapped_column(String(1000))
    summary: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, object]] = mapped_column(MutableDict.as_mutable(JSON), default=dict)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    scan_run = relationship("ScanRun", back_populates="evidence")


class ScanFinding(Base):
    __tablename__ = "scan_findings"
    __table_args__ = (UniqueConstraint("scan_run_id", "dedup_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    dedup_key: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(120), index=True)
    severity: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[str] = mapped_column(String(32), index=True)
    summary: Mapped[str] = mapped_column(Text)
    remediation: Mapped[str] = mapped_column(Text)
    asset_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    evidence_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    scan_run = relationship("ScanRun", back_populates="findings")


class ScanFailure(Base):
    __tablename__ = "scan_failures"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(120), index=True)
    message: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    retryable: Mapped[bool] = mapped_column(default=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    scan_run = relationship("ScanRun", back_populates="failures")
