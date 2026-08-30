"""Interface-neutral application service for one URL to one report."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import InputValidationError, ResourceNotFoundError
from app.core.settings import Settings, get_settings
from app.db.bootstrap import session_scope
from app.models.coverage_difference import CoverageDifference
from app.models.credential_profile import CredentialProfile
from app.models.enums import AssessmentMode, CompletenessStatus, ContextKind
from app.models.scan_context import ScanContext
from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES, ScanRun, ScanRunStage
from app.models.target import Target
from app.schemas.assessment import (
    AssessmentRunView,
    AssessmentStartRequest,
    CoverageDifferenceView,
)
from app.schemas.scan import (
    ScanContextView,
    ScanFailureView,
    ScanOptions,
    ScanRunStatus,
    ScanRunView,
    ScanStageView,
)
from app.services.scan_network import HttpScanGateway, TargetNetworkPolicy
from app.services.scan_pipeline import STAGES, ScanPipeline


def create_assessment_stages(session: Session, run: ScanRun) -> None:
    """原子创建固定八阶段，并显式标记 quick 不适用的阶段。"""

    now = datetime.now(timezone.utc)
    stages = []
    for position, name in enumerate(STAGES, start=1):
        values: dict[str, object] = {
            "scan_run_id": run.id,
            "name": name,
            "position": position,
        }
        if run.mode == AssessmentMode.QUICK.value:
            if name == "establish_contexts":
                values.update(
                    status="completed",
                    attempt=1,
                    summary="Created the anonymous quick-scan context.",
                    started_at=now,
                    finished_at=now,
                )
            elif name in {"compare_coverage", "replay_authorization"}:
                values.update(
                    status="skipped",
                    summary="Not applicable to a single-context quick assessment.",
                    finished_at=now,
                )
        stages.append(ScanRunStage(**values))
    session.add_all(stages)


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
        assessment = self.start_assessment(
            AssessmentStartRequest(
                url=url,
                mode=AssessmentMode.QUICK,
                options=options or ScanOptions(),
            )
        )
        return ScanRunView.model_validate(assessment.model_dump())

    def start_assessment(self, request: AssessmentStartRequest) -> AssessmentRunView:
        resolved = self._resolve_options(request.options)
        normalized = self.pipeline.policy.normalize_url(request.url)
        with self._start_lock, session_scope() as session:
            target_id = self._resolve_target_id(session, request)
            self._validate_source_run(session, request, target_id)
            active_key = self._active_key(normalized, request, resolved, target_id)
            existing = session.scalar(select(ScanRun).where(ScanRun.active_key == active_key))
            if existing is not None:
                return self._assessment_view(existing)
            run = ScanRun(
                mode=request.mode.value,
                target_id=target_id,
                source_run_id=request.source_run_id,
                input_url=request.url.strip(),
                normalized_url=normalized,
                active_key=active_key,
                status=ScanRunStatus.QUEUED.value,
                current_stage="queued",
                progress=0,
                options=resolved.model_dump(),
                active_checks_enabled=request.active_checks_enabled,
                authorization_confirmed_at=(
                    datetime.now(timezone.utc) if request.authorization_confirmed else None
                ),
                authorization_confirmed_by=(
                    request.authorization_confirmed_by if request.authorization_confirmed else None
                ),
                completeness=(
                    CompletenessStatus.LEGACY_SINGLE_CONTEXT.value
                    if request.mode == AssessmentMode.QUICK
                    else CompletenessStatus.PENDING.value
                ),
            )
            session.add(run)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(select(ScanRun).where(ScanRun.active_key == active_key))
                if existing is not None:
                    return self._assessment_view(existing)
                raise
            session.add_all(self._assessment_contexts(run, request))
            create_assessment_stages(session, run)
            session.commit()
            run_id = run.id
        callback = (
            self.execute_scan
            if request.mode == AssessmentMode.QUICK
            else self.execute_authenticated_assessment
        )
        try:
            self.dispatcher.submit(run_id, callback)
        except Exception:
            self._mark_dispatch_failed(run_id)
            raise
        return self.get_assessment(run_id)

    def execute_scan(self, scan_run_id: int) -> None:
        with session_scope() as session:
            self.pipeline.execute(session, scan_run_id)

    def execute_authenticated_assessment(self, scan_run_id: int) -> None:
        with session_scope() as session:
            try:
                self.pipeline.execute_authenticated(session, scan_run_id)
            finally:
                self.pipeline.cleanup_temporary_context_secrets(session, scan_run_id)

    def cancel_scan(self, scan_run_id: int) -> ScanRunView:
        """立即标记一个尚未结束的扫描为已中断，保留已写入的证据。"""

        with session_scope() as session:
            run = self._get(session, scan_run_id)
            if run.status not in TERMINAL_SCAN_RUN_STATUSES:
                self._mark_interrupted(run)
            return self._view(run)

    def cancel_assessment(self, scan_run_id: int) -> AssessmentRunView:
        with session_scope() as session:
            run = self._get(session, scan_run_id)
            if run.mode != AssessmentMode.AUTHENTICATED_COVERAGE.value:
                raise InputValidationError("Only authenticated coverage runs are assessments.")
            if run.status not in TERMINAL_SCAN_RUN_STATUSES:
                self._mark_interrupted(run)
                self.pipeline.cleanup_temporary_context_secrets(session, run.id)
            return self._assessment_view(run)

    def interrupt_active_scans(self) -> int:
        """在服务启动时收敛上次异常退出遗留的活动扫描。"""

        with session_scope() as session:
            active_ids = list(
                session.scalars(
                    select(ScanRun.id).where(
                        ScanRun.status.in_(
                            [ScanRunStatus.QUEUED.value, ScanRunStatus.RUNNING.value]
                        )
                    )
                )
            )
            for scan_run_id in active_ids:
                run = self._get(session, scan_run_id)
                self._mark_interrupted(run)
                if run.mode == AssessmentMode.AUTHENTICATED_COVERAGE.value:
                    self.pipeline.cleanup_temporary_context_secrets(session, run.id)
            return len(active_ids)

    def get_scan(self, scan_run_id: int) -> ScanRunView:
        with session_scope() as session:
            run = self._get(session, scan_run_id)
            return self._view(run)

    def get_assessment(self, scan_run_id: int) -> AssessmentRunView:
        with session_scope() as session:
            run = self._get(session, scan_run_id)
            return self._assessment_view(run)

    def list_coverage_differences(
        self,
        scan_run_id: int,
    ) -> list[CoverageDifferenceView]:
        with session_scope() as session:
            run = self._get(session, scan_run_id)
            if run.mode != AssessmentMode.AUTHENTICATED_COVERAGE.value:
                raise InputValidationError("Only authenticated coverage runs have differences.")
            differences = list(
                session.scalars(
                    select(CoverageDifference)
                    .where(CoverageDifference.scan_run_id == scan_run_id)
                    .order_by(CoverageDifference.identity_key)
                )
            )
            return [CoverageDifferenceView.model_validate(item) for item in differences]

    def list_scans(self, limit: int = 25) -> list[ScanRunView]:
        with session_scope() as session:
            runs = list(
                session.scalars(
                    select(ScanRun)
                    .options(
                        selectinload(ScanRun.stages),
                        selectinload(ScanRun.contexts),
                        selectinload(ScanRun.failures),
                        selectinload(ScanRun.assets),
                        selectinload(ScanRun.evidence),
                        selectinload(ScanRun.findings),
                        selectinload(ScanRun.coverage_differences),
                        selectinload(ScanRun.replay_executions),
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
            timeout_seconds
            if timeout_seconds is not None
            else self.settings.scan_overall_timeout_seconds + 10
        )
        while time.monotonic() < deadline:
            run = self.get_scan(scan_run_id)
            if run.status in TERMINAL_SCAN_RUN_STATUSES:
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

    def _mark_dispatch_failed(self, scan_run_id: int) -> None:
        with session_scope() as session:
            run = self._get(session, scan_run_id)
            now = datetime.now(timezone.utc)
            run.status = ScanRunStatus.FAILED.value
            run.current_stage = ScanRunStatus.FAILED.value
            run.error_code = "dispatch_failed"
            run.error_message = "Scan could not be submitted for execution."
            run.finished_at = now
            run.active_key = None
            for stage in run.stages:
                if stage.status == "pending":
                    stage.status = "skipped"
                    stage.summary = "Skipped because scan dispatch failed."
                    stage.finished_at = now
            if run.mode == AssessmentMode.AUTHENTICATED_COVERAGE.value:
                self.pipeline.cleanup_temporary_context_secrets(session, run.id)

    def _get(self, session, scan_run_id: int) -> ScanRun:
        run = session.scalar(
            select(ScanRun)
            .where(ScanRun.id == scan_run_id)
            .options(
                selectinload(ScanRun.stages),
                selectinload(ScanRun.contexts),
                selectinload(ScanRun.failures),
                selectinload(ScanRun.assets),
                selectinload(ScanRun.evidence),
                selectinload(ScanRun.findings),
                selectinload(ScanRun.coverage_differences),
                selectinload(ScanRun.replay_executions),
            )
        )
        if run is None:
            raise ResourceNotFoundError(f"Scan run {scan_run_id} was not found.")
        return run

    def _mark_interrupted(self, run: ScanRun) -> None:
        now = datetime.now(timezone.utc)
        run.status = ScanRunStatus.INTERRUPTED.value
        run.current_stage = ScanRunStatus.INTERRUPTED.value
        run.error_code = "scan_cancelled"
        run.error_message = "Scan was cancelled before completion."
        run.finished_at = now
        run.active_key = None
        for stage in run.stages:
            if stage.status == "running":
                stage.status = "interrupted"
                stage.summary = "Interrupted before this stage completed."
                stage.finished_at = now
            elif stage.status == "pending":
                stage.status = "skipped"
                stage.summary = "Skipped because the scan was cancelled."
                stage.finished_at = now
        if run.mode == AssessmentMode.QUICK.value:
            for context in run.contexts:
                if context.kind != ContextKind.ANONYMOUS.value:
                    continue
                context.status = ScanRunStatus.INTERRUPTED.value
                context.collection_status = ScanRunStatus.INTERRUPTED.value
                context.error_code = "scan_cancelled"
                context.error_message = "Scan was cancelled before collection completed."
                context.finished_at = now

    def _view(self, run: ScanRun) -> ScanRunView:
        return ScanRunView(**self._view_data(run))

    def _assessment_view(self, run: ScanRun) -> AssessmentRunView:
        context_counts = {kind.value: 0 for kind in ContextKind}
        for context in run.contexts:
            context_counts[context.kind] += 1
        return AssessmentRunView(
            **self._view_data(run),
            completeness=run.completeness,
            active_checks_enabled=run.active_checks_enabled,
            authorization_confirmed_at=run.authorization_confirmed_at,
            authorization_confirmed_by=run.authorization_confirmed_by,
            context_counts=context_counts,
            difference_count=len(run.coverage_differences),
            replay_count=len(run.replay_executions),
        )

    def _view_data(self, run: ScanRun) -> dict[str, object]:
        return {
            "id": run.id,
            "mode": run.mode,
            "target_id": run.target_id,
            "source_run_id": run.source_run_id,
            "input_url": run.input_url,
            "normalized_url": run.normalized_url,
            "status": run.status,
            "current_stage": run.current_stage,
            "progress": run.progress,
            "retry_count": run.retry_count,
            "report_path": run.report_path,
            "error_code": run.error_code,
            "error_message": run.error_message,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "report_generated_at": run.report_generated_at,
            "created_at": run.created_at,
            "contexts": [ScanContextView.model_validate(item) for item in run.contexts],
            "stages": [
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
            "failures": [
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
            "asset_count": len(run.assets),
            "evidence_count": len(run.evidence),
            "finding_count": len(run.findings),
        }

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

    def _active_key(
        self,
        normalized_url: str,
        request: AssessmentStartRequest,
        options: ScanOptions,
        target_id: int | None,
    ) -> str:
        payload = json.dumps(
            {
                "url": normalized_url,
                "mode": request.mode.value,
                "target_id": target_id,
                "user_profile_id": request.user_profile_id,
                "admin_profile_id": request.admin_profile_id,
                "active_checks_enabled": request.active_checks_enabled,
                "source_run_id": request.source_run_id,
                "options": options.model_dump(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _resolve_target_id(self, session, request: AssessmentStartRequest) -> int | None:
        if request.mode == AssessmentMode.QUICK:
            if request.target_id is not None and session.get(Target, request.target_id) is None:
                raise ResourceNotFoundError(f"Target {request.target_id} was not found.")
            return request.target_id

        user_profile = session.get(CredentialProfile, request.user_profile_id)
        admin_profile = session.get(CredentialProfile, request.admin_profile_id)
        if user_profile is None:
            raise ResourceNotFoundError(
                f"Credential profile {request.user_profile_id} was not found."
            )
        if admin_profile is None:
            raise ResourceNotFoundError(
                f"Credential profile {request.admin_profile_id} was not found."
            )
        if user_profile.target_id != admin_profile.target_id:
            raise InputValidationError("user/admin 凭证档案必须属于同一 target。")
        if request.target_id is not None and request.target_id != user_profile.target_id:
            raise InputValidationError("请求 target 与凭证档案 target 不一致。")
        return user_profile.target_id

    def _validate_source_run(
        self, session, request: AssessmentStartRequest, target_id: int | None
    ) -> None:
        if request.source_run_id is None:
            return
        if request.mode != AssessmentMode.AUTHENTICATED_COVERAGE:
            raise InputValidationError("source_run_id 只用于升级到 authenticated_coverage。")
        source = session.get(ScanRun, request.source_run_id)
        if source is None:
            raise ResourceNotFoundError(f"Scan run {request.source_run_id} was not found.")
        if (
            source.mode != AssessmentMode.QUICK.value
            or source.status not in TERMINAL_SCAN_RUN_STATUSES
            or source.target_id != target_id
        ):
            raise InputValidationError("source_run_id 必须引用同一 target 的已终态 quick run。")

    def _assessment_contexts(
        self, run: ScanRun, request: AssessmentStartRequest
    ) -> list[ScanContext]:
        contexts = [
            ScanContext(
                scan_run_id=run.id,
                kind=ContextKind.ANONYMOUS.value,
                status="pending",
            )
        ]
        if request.mode == AssessmentMode.AUTHENTICATED_COVERAGE:
            contexts.extend(
                [
                    ScanContext(
                        scan_run_id=run.id,
                        kind=ContextKind.USER.value,
                        credential_profile_id=request.user_profile_id,
                        status="pending",
                    ),
                    ScanContext(
                        scan_run_id=run.id,
                        kind=ContextKind.ADMIN.value,
                        credential_profile_id=request.admin_profile_id,
                        status="pending",
                    ),
                ]
            )
        return contexts
