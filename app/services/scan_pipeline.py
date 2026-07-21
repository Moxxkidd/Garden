"""Persisted six-stage URL-to-report execution pipeline."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import InputValidationError, TargetPolicyError
from app.models.scan_run import (
    ScanAsset,
    ScanEvidence,
    ScanFailure,
    ScanFinding,
    ScanRun,
    ScanRunStage,
)
from app.redaction.service import RedactionService
from app.schemas.scan import FetchResult, ScanOptions, ScanRunStatus, ScanStageName
from app.services.scan_analysis import PassiveScanAnalyzer
from app.services.scan_network import FetchError, HttpScanGateway, TargetNetworkPolicy
from app.services.scan_reporting import ScanReportService

STAGES = [stage.value for stage in ScanStageName]
PROGRESS_AFTER_STAGE = {
    ScanStageName.VALIDATE.value: 8,
    ScanStageName.DISCOVER.value: 20,
    ScanStageName.COLLECT.value: 58,
    ScanStageName.NORMALIZE.value: 68,
    ScanStageName.ANALYZE.value: 86,
    ScanStageName.REPORT.value: 100,
}


class OverallScanTimeout(RuntimeError):
    pass


class ScanPipeline:
    def __init__(
        self,
        *,
        policy: TargetNetworkPolicy,
        gateway: HttpScanGateway,
        analyzer: PassiveScanAnalyzer | None = None,
        report_service: ScanReportService | None = None,
        redaction_service: RedactionService | None = None,
        clock=time.monotonic,
    ) -> None:
        self.policy = policy
        self.gateway = gateway
        self.analyzer = analyzer or PassiveScanAnalyzer()
        self.report_service = report_service or ScanReportService()
        self.redaction_service = redaction_service or RedactionService()
        self.clock = clock

    def execute(self, session: Session, scan_run_id: int) -> None:
        run = session.get(ScanRun, scan_run_id)
        if run is None or run.status not in {
            ScanRunStatus.QUEUED.value,
            ScanRunStatus.RUNNING.value,
        }:
            return
        options = ScanOptions.model_validate(run.options)
        deadline = self.clock() + float(options.overall_timeout_seconds or 90)
        run.status = ScanRunStatus.RUNNING.value
        run.started_at = run.started_at or datetime.now(timezone.utc)
        session.commit()
        try:
            self._run_stage(
                session,
                run,
                ScanStageName.VALIDATE.value,
                deadline,
                lambda: self._validate(run),
            )
            entry = self._run_stage(
                session,
                run,
                ScanStageName.DISCOVER.value,
                deadline,
                lambda: self._discover(session, run, options),
            )
            self._run_stage(
                session,
                run,
                ScanStageName.COLLECT.value,
                deadline,
                lambda: self._collect(session, run, entry, options, deadline),
            )
            self._run_stage(
                session,
                run,
                ScanStageName.NORMALIZE.value,
                deadline,
                lambda: self._normalize(session, run),
            )
            self._run_stage(
                session,
                run,
                ScanStageName.ANALYZE.value,
                deadline,
                lambda: self._analyze(session, run),
            )
            self._run_stage(
                session,
                run,
                ScanStageName.REPORT.value,
                deadline,
                lambda: self._report(session, run),
            )
            session.refresh(run)
            run.status = (
                ScanRunStatus.COMPLETED_WITH_WARNINGS.value
                if run.failures
                else ScanRunStatus.COMPLETED.value
            )
            run.current_stage = "finished"
            run.progress = 100
            run.finished_at = datetime.now(timezone.utc)
            run.active_key = None
            session.commit()
        except Exception as error:  # noqa: BLE001 - stage boundary records diagnostics
            session.rollback()
            run = session.get(ScanRun, scan_run_id)
            if run is None:
                raise
            stage_name = run.current_stage if run.current_stage in STAGES else "pipeline"
            code, retryable, attempts, url = self._error_details(error, run.normalized_url)
            self._record_failure(
                session,
                run,
                stage=stage_name,
                code=code,
                message=str(error),
                url=url,
                retryable=retryable,
                attempt=attempts,
            )
            stage = self._stage(session, run.id, stage_name)
            if stage is not None:
                stage.status = "failed"
                stage.error_code = code
                stage.error_message = str(error)
                stage.finished_at = datetime.now(timezone.utc)
            run.status = ScanRunStatus.FAILED.value
            run.error_code = code
            run.error_message = str(error)
            run.finished_at = datetime.now(timezone.utc)
            run.active_key = None
            session.commit()
            self._try_failure_report(session, run)

    def _run_stage(self, session, run, name, deadline, operation):
        self._check_deadline(deadline)
        stage = self._stage(session, run.id, name)
        if stage is None:
            raise RuntimeError(f"Missing persisted stage '{name}'.")
        stage.status = "running"
        stage.attempt += 1
        stage.started_at = datetime.now(timezone.utc)
        run.current_stage = name
        session.commit()
        result, summary = operation()
        self._check_deadline(deadline)
        stage = self._stage(session, run.id, name)
        stage.status = "completed"
        stage.summary = summary
        stage.finished_at = datetime.now(timezone.utc)
        run = session.get(ScanRun, run.id)
        run.progress = PROGRESS_AFTER_STAGE[name]
        session.commit()
        return result

    def _validate(self, run: ScanRun):
        result = self.policy.preflight(run.normalized_url)
        run.normalized_url = result.normalized_url
        summary = (
            f"Network preflight passed with policy {result.policy}; "
            f"resolved {len(result.resolved_addresses)} address(es); "
            f"proxy={'enabled' if result.proxy_enabled else 'bypassed/disabled'}."
        )
        return result, summary

    def _discover(self, session: Session, run: ScanRun, options: ScanOptions):
        result = self.gateway.fetch(run.normalized_url, options)
        self._persist_fetch(session, run, result, depth=0)
        return result, (
            f"Fetched entry URL with HTTP {result.status_code}; "
            f"discovered {len(result.discovered_urls)} linked URL(s)."
        )

    def _collect(
        self,
        session: Session,
        run: ScanRun,
        entry: FetchResult,
        options: ScanOptions,
        deadline: float,
    ):
        queue = [(url, 1) for url in entry.discovered_urls]
        seen = {entry.final_url, run.normalized_url}
        origin = self._origin(entry.final_url)
        collected = 1
        skipped_external = 0
        while queue and collected < options.max_pages:
            self._check_deadline(deadline)
            url, depth = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if self._origin(url) != origin:
                skipped_external += 1
                continue
            if depth > options.max_depth:
                continue
            try:
                result = self.gateway.fetch(url, options)
            except (FetchError, InputValidationError, TargetPolicyError) as error:
                code, retryable, attempts, failed_url = self._error_details(error, url)
                self._record_failure(
                    session,
                    run,
                    stage=ScanStageName.COLLECT.value,
                    code=code,
                    message=str(error),
                    url=failed_url,
                    retryable=retryable,
                    attempt=attempts,
                )
                session.commit()
                continue
            self._persist_fetch(session, run, result, depth=depth)
            collected += 1
            if depth < options.max_depth:
                queue.extend((candidate, depth + 1) for candidate in result.discovered_urls)
            session.commit()
        remaining = len(queue)
        if remaining:
            self._record_failure(
                session,
                run,
                stage=ScanStageName.COLLECT.value,
                code="coverage_limit_reached",
                message=f"Stopped with {remaining} queued URL(s) because a scan bound was reached.",
                url=None,
                retryable=False,
                attempt=1,
            )
        return None, (
            f"Recorded {collected} same-origin page(s); skipped {skipped_external} external "
            f"link(s); {remaining} URL(s) remained outside configured bounds."
        )

    def _normalize(self, session: Session, run: ScanRun):
        assets = list(session.scalars(select(ScanAsset).where(ScanAsset.scan_run_id == run.id)))
        evidence = list(
            session.scalars(select(ScanEvidence).where(ScanEvidence.scan_run_id == run.id))
        )
        if not assets:
            self._record_failure(
                session,
                run,
                stage=ScanStageName.NORMALIZE.value,
                code="no_assets",
                message="The scan completed collection without any normalized assets.",
                url=run.normalized_url,
                retryable=False,
                attempt=1,
            )
        return None, f"Normalized {len(assets)} asset(s) and {len(evidence)} evidence record(s)."

    def _analyze(self, session: Session, run: ScanRun):
        assets = list(session.scalars(select(ScanAsset).where(ScanAsset.scan_run_id == run.id)))
        evidence = list(
            session.scalars(select(ScanEvidence).where(ScanEvidence.scan_run_id == run.id))
        )
        candidates = self.analyzer.analyze(assets, evidence)
        now = datetime.now(timezone.utc)
        for candidate in candidates:
            existing = session.scalar(
                select(ScanFinding).where(
                    ScanFinding.scan_run_id == run.id,
                    ScanFinding.dedup_key == candidate.dedup_key,
                )
            )
            if existing is not None:
                continue
            session.add(
                ScanFinding(
                    scan_run_id=run.id,
                    created_at=now,
                    **candidate.model_dump(),
                )
            )
        session.flush()
        return None, f"Created {len(candidates)} passive, explainable finding(s)."

    def _report(self, session: Session, run: ScanRun):
        path = self.report_service.generate(session, run.id)
        return path, f"Generated structured Markdown report at {path}."

    def _persist_fetch(
        self, session: Session, run: ScanRun, result: FetchResult, *, depth: int
    ) -> None:
        asset = session.scalar(
            select(ScanAsset).where(
                ScanAsset.scan_run_id == run.id,
                ScanAsset.asset_type == "page",
                ScanAsset.url == result.final_url,
            )
        )
        now = datetime.now(timezone.utc)
        attributes = {
            "content_type": result.content_type,
            "elapsed_ms": result.elapsed_ms,
            "attempts": result.attempts,
            "depth": depth,
            "redirects": result.redirects,
            "discovered_urls": result.discovered_urls,
        }
        if asset is None:
            asset = ScanAsset(
                scan_run_id=run.id,
                asset_type="page",
                url=result.final_url,
                method="GET",
                status_code=result.status_code,
                title=result.title,
                attributes=attributes,
                discovered_at=now,
            )
            session.add(asset)
            session.flush()
        headers = self.redaction_service.redact_mapping(result.headers)
        preview = self.redaction_service.redact_text(result.body_text, limit=1200)
        session.add(
            ScanEvidence(
                scan_run_id=run.id,
                asset_id=asset.id,
                evidence_type="http_response",
                title=f"GET {result.final_url} -> HTTP {result.status_code}",
                source_url=result.final_url,
                summary=(
                    f"HTTP {result.status_code}; content-type={result.content_type or '-'}; "
                    f"elapsed={result.elapsed_ms}ms; attempts={result.attempts}."
                ),
                data={"headers": headers, "preview": preview},
                collected_at=now,
            )
        )
        run.retry_count += max(0, result.attempts - 1)
        session.flush()

    def _record_failure(
        self,
        session: Session,
        run: ScanRun,
        *,
        stage: str,
        code: str,
        message: str,
        url: str | None,
        retryable: bool,
        attempt: int,
    ) -> None:
        session.add(
            ScanFailure(
                scan_run_id=run.id,
                stage=stage,
                code=code,
                message=message,
                url=url,
                retryable=retryable,
                attempt=attempt,
                occurred_at=datetime.now(timezone.utc),
            )
        )
        run.retry_count += max(0, attempt - 1)

    def _try_failure_report(self, session: Session, run: ScanRun) -> None:
        try:
            report_stage = self._stage(session, run.id, ScanStageName.REPORT.value)
            if report_stage and report_stage.status == "pending":
                report_stage.status = "running"
                report_stage.attempt += 1
                report_stage.started_at = datetime.now(timezone.utc)
            path = self.report_service.generate(session, run.id)
            if report_stage:
                report_stage.status = "completed"
                report_stage.summary = f"Generated diagnostic failure report at {path}."
                report_stage.finished_at = datetime.now(timezone.utc)
            session.commit()
        except Exception:  # noqa: BLE001 - original failure remains authoritative
            session.rollback()

    def _stage(self, session: Session, run_id: int, name: str) -> ScanRunStage | None:
        return session.scalar(
            select(ScanRunStage).where(
                ScanRunStage.scan_run_id == run_id, ScanRunStage.name == name
            )
        )

    def _check_deadline(self, deadline: float) -> None:
        if self.clock() > deadline:
            raise OverallScanTimeout("The overall scan timeout was exceeded.")

    def _origin(self, url: str) -> tuple[str, str, int | None]:
        parsed = urlparse(url)
        effective_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return parsed.scheme, parsed.hostname or "", effective_port

    def _error_details(self, error: Exception, fallback_url: str):
        if isinstance(error, FetchError):
            return error.code, error.retryable, error.attempts, error.url
        if isinstance(error, OverallScanTimeout):
            return "overall_timeout", False, 1, fallback_url
        if isinstance(error, InputValidationError):
            return "network_configuration_invalid", False, 1, fallback_url
        if isinstance(error, TargetPolicyError):
            return "target_policy_blocked", False, 1, fallback_url
        return "stage_execution_failed", False, 1, fallback_url
