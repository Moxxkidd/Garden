"""Interface-neutral application service for one URL to one report."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.errors import ResourceNotFoundError
from app.core.settings import Settings, get_settings
from app.db.bootstrap import session_scope
from app.models.scan_run import ScanRun, ScanRunStage
from app.schemas.scan import (
    ScanFailureView,
    ScanOptions,
    ScanRunStatus,
    ScanRunView,
    ScanStageView,
)
from app.services.scan_network import HttpScanGateway, TargetNetworkPolicy
from app.services.scan_pipeline import STAGES, ScanPipeline


class ScanDispatcher(Protocol):
    def submit(self, scan_run_id: int, callback) -> None: ...


class InlineScanDispatcher:
    def submit(self, scan_run_id: int, callback) -> None:
        callback(scan_run_id)

    def shutdown(self) -> None:
        return None


class ThreadedScanDispatcher:
    def __init__(self, max_workers: int) -> None:
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="garden-scan"
        )

    def submit(self, scan_run_id: int, callback) -> None:
        self.executor.submit(callback, scan_run_id)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)


class ScanApplicationService:
    """The sole orchestration entry used by CLI, Web, and HTTP adapters."""

    _start_lock = threading.Lock()

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        pipeline: ScanPipeline | None = None,
        dispatcher: ScanDispatcher | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        if pipeline is None:
            policy = TargetNetworkPolicy(self.settings)
            pipeline = ScanPipeline(policy=policy, gateway=HttpScanGateway(policy))
        self.pipeline = pipeline
        self.dispatcher = dispatcher or ThreadedScanDispatcher(
            self.settings.scan_max_concurrent_tasks
        )

    def start_scan(self, url: str, options: ScanOptions | None = None) -> ScanRunView:
        resolved = self._resolve_options(options or ScanOptions())
        normalized = self.pipeline.policy.normalize_url(url)
        active_key = self._active_key(normalized, resolved)
        with self._start_lock, session_scope() as session:
            existing = session.scalar(select(ScanRun).where(ScanRun.active_key == active_key))
            if existing is not None:
                return self._view(existing)
            run = ScanRun(
                input_url=url.strip(),
                normalized_url=normalized,
                active_key=active_key,
                status=ScanRunStatus.QUEUED.value,
                current_stage="queued",
                progress=0,
                options=resolved.model_dump(),
            )
            session.add(run)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(select(ScanRun).where(ScanRun.active_key == active_key))
                if existing is not None:
                    return self._view(existing)
                raise
            session.add_all(
                [
                    ScanRunStage(scan_run_id=run.id, name=name, position=index)
                    for index, name in enumerate(STAGES, start=1)
                ]
            )
            session.commit()
            run_id = run.id
        self.dispatcher.submit(run_id, self.execute_scan)
        return self.get_scan(run_id)

    def execute_scan(self, scan_run_id: int) -> None:
        with session_scope() as session:
            self.pipeline.execute(session, scan_run_id)

    def get_scan(self, scan_run_id: int) -> ScanRunView:
        with session_scope() as session:
            run = self._get(session, scan_run_id)
            return self._view(run)

    def list_scans(self, limit: int = 25) -> list[ScanRunView]:
        with session_scope() as session:
            runs = list(
                session.scalars(
                    select(ScanRun)
                    .options(
                        selectinload(ScanRun.stages),
                        selectinload(ScanRun.failures),
                        selectinload(ScanRun.assets),
                        selectinload(ScanRun.evidence),
                        selectinload(ScanRun.findings),
                    )
                    .order_by(ScanRun.id.desc())
                    .limit(limit)
                )
            )
            return [self._view(run) for run in runs]

    def wait_for_completion(
        self, scan_run_id: int, *, timeout_seconds: float | None = None
    ) -> ScanRunView:
        deadline = time.monotonic() + (
            timeout_seconds or self.settings.scan_overall_timeout_seconds + 10
        )
        while time.monotonic() < deadline:
            run = self.get_scan(scan_run_id)
            if run.status in {
                ScanRunStatus.COMPLETED.value,
                ScanRunStatus.COMPLETED_WITH_WARNINGS.value,
                ScanRunStatus.FAILED.value,
            }:
                return run
            time.sleep(0.05)
        return self.get_scan(scan_run_id)

    def read_report(self, scan_run_id: int) -> str:
        view = self.get_scan(scan_run_id)
        if not view.report_path:
            raise ResourceNotFoundError(f"Scan run {scan_run_id} does not have a report yet.")
        with open(view.report_path, encoding="utf-8") as handle:
            return handle.read()

    def shutdown(self) -> None:
        shutdown = getattr(self.dispatcher, "shutdown", None)
        if shutdown is not None:
            shutdown()

    def _get(self, session, scan_run_id: int) -> ScanRun:
        run = session.scalar(
            select(ScanRun)
            .where(ScanRun.id == scan_run_id)
            .options(
                selectinload(ScanRun.stages),
                selectinload(ScanRun.failures),
                selectinload(ScanRun.assets),
                selectinload(ScanRun.evidence),
                selectinload(ScanRun.findings),
            )
        )
        if run is None:
            raise ResourceNotFoundError(f"Scan run {scan_run_id} was not found.")
        return run

    def _view(self, run: ScanRun) -> ScanRunView:
        return ScanRunView(
            id=run.id,
            input_url=run.input_url,
            normalized_url=run.normalized_url,
            status=run.status,
            current_stage=run.current_stage,
            progress=run.progress,
            retry_count=run.retry_count,
            report_path=run.report_path,
            error_code=run.error_code,
            error_message=run.error_message,
            started_at=run.started_at,
            finished_at=run.finished_at,
            report_generated_at=run.report_generated_at,
            created_at=run.created_at,
            stages=[
                ScanStageView(
                    name=stage.name,
                    position=stage.position,
                    status=stage.status,
                    attempt=stage.attempt,
                    summary=stage.summary,
                    error_code=stage.error_code,
                    error_message=stage.error_message,
                    started_at=stage.started_at,
                    finished_at=stage.finished_at,
                )
                for stage in sorted(run.stages, key=lambda item: item.position)
            ],
            failures=[
                ScanFailureView(
                    stage=item.stage,
                    code=item.code,
                    message=item.message,
                    url=item.url,
                    retryable=item.retryable,
                    attempt=item.attempt,
                    occurred_at=item.occurred_at,
                )
                for item in sorted(run.failures, key=lambda item: item.id)
            ],
            asset_count=len(run.assets),
            evidence_count=len(run.evidence),
            finding_count=len(run.findings),
        )

    def _resolve_options(self, options: ScanOptions) -> ScanOptions:
        return options.model_copy(
            update={
                "request_timeout_seconds": options.request_timeout_seconds
                or self.settings.scan_request_timeout_seconds,
                "overall_timeout_seconds": options.overall_timeout_seconds
                or self.settings.scan_overall_timeout_seconds,
                "retry_attempts": (
                    options.retry_attempts
                    if options.retry_attempts is not None
                    else self.settings.scan_retry_attempts
                ),
            }
        )

    def _active_key(self, normalized_url: str, options: ScanOptions) -> str:
        payload = json.dumps(
            {"url": normalized_url, "options": options.model_dump()},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()
