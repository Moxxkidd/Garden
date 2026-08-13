"""Quick URL-to-report execution mapped onto the unified assessment stages."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import InputValidationError, TargetPolicyError
from app.models.enums import AssessmentMode, CompletenessStatus, ContextKind
from app.models.scan_context import ScanContext
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
from app.services.context_collection import (
    ContextCollectionService,
    ContextCollectionSummary,
    DefaultContextCollectionGateway,
)
from app.services.context_establishment import ContextEstablishmentService
from app.services.coverage_identity import canonical_asset_identity, redacted_observed_url
from app.services.scan_analysis import PassiveScanAnalyzer
from app.services.scan_network import FetchError, HttpScanGateway, TargetNetworkPolicy
from app.services.scan_reporting import ScanReportService

STAGES = [stage.value for stage in ScanStageName]
PROGRESS_AFTER_STAGE = {
    ScanStageName.VALIDATE.value: 8,
    ScanStageName.ESTABLISH_CONTEXTS.value: 18,
    ScanStageName.COLLECT.value: 58,
    ScanStageName.NORMALIZE.value: 68,
    ScanStageName.ANALYZE.value: 86,
    ScanStageName.REPORT.value: 100,
}


class OverallScanTimeout(RuntimeError):
    pass


class ScanInterrupted(RuntimeError):
    """A persisted cancellation signal observed at a safe pipeline boundary."""


class ScanPipeline:
    def __init__(
        self,
        *,
        policy: TargetNetworkPolicy,
        gateway: HttpScanGateway,
        analyzer: PassiveScanAnalyzer | None = None,
        report_service: ScanReportService | None = None,
        redaction_service: RedactionService | None = None,
        context_service: ContextEstablishmentService | None = None,
        context_collection_service: ContextCollectionService | None = None,
        clock=time.monotonic,
    ) -> None:
        self.policy = policy
        self.gateway = gateway
        self.analyzer = analyzer or PassiveScanAnalyzer()
        self.report_service = report_service or ScanReportService()
        self.redaction_service = redaction_service or RedactionService()
        self.context_service = context_service or ContextEstablishmentService()
        self.context_collection_service = context_collection_service or ContextCollectionService(
            gateway=DefaultContextCollectionGateway(http_gateway=gateway)
        )
        self.clock = clock

    def establish_contexts(self, session: Session, scan_run_id: int) -> list[ScanContext]:
        """Execute only the authenticated context-establishment stage."""

        run = session.get(ScanRun, scan_run_id)
        if run is None:
            raise InputValidationError(f"Scan run {scan_run_id} was not found.")
        if run.mode != AssessmentMode.AUTHENTICATED_COVERAGE.value:
            raise InputValidationError("Only authenticated coverage runs establish three contexts.")
        stage = self._stage(session, run.id, ScanStageName.ESTABLISH_CONTEXTS.value)
        if stage is None:
            raise RuntimeError("Missing persisted stage 'establish_contexts'.")

        stage.status = "running"
        stage.attempt += 1
        stage.started_at = datetime.now(timezone.utc)
        run.current_stage = ScanStageName.ESTABLISH_CONTEXTS.value
        session.flush()
        try:
            contexts = self.context_service.establish(session, run)
        except Exception as error:
            session.rollback()
            run = session.get(ScanRun, scan_run_id)
            stage = self._stage(session, scan_run_id, ScanStageName.ESTABLISH_CONTEXTS.value)
            if run is not None:
                run.status = ScanRunStatus.INCOMPLETE.value
                run.completeness = CompletenessStatus.INCOMPLETE.value
                run.error_code = "context_establishment_invalid"
                run.error_message = str(error)
                run.finished_at = datetime.now(timezone.utc)
            if stage is not None:
                stage.status = "failed"
                stage.error_code = "context_establishment_invalid"
                stage.error_message = str(error)
                stage.finished_at = datetime.now(timezone.utc)
            session.commit()
            raise

        if run.status == ScanRunStatus.INCOMPLETE.value:
            stage.status = "failed"
            stage.error_code = run.error_code
            stage.error_message = run.error_message
            stage.summary = "One or more required authentication contexts failed."
        else:
            stage.status = "completed"
            stage.summary = "Established anonymous, user, and admin contexts."
            run.progress = PROGRESS_AFTER_STAGE[ScanStageName.ESTABLISH_CONTEXTS.value]
        stage.finished_at = datetime.now(timezone.utc)
        session.commit()
        return contexts

    def collect_contexts(
        self,
        session: Session,
        scan_run_id: int,
    ) -> list[ContextCollectionSummary]:
        """Execute context collection independently so one failure retains other results."""

        run = session.get(ScanRun, scan_run_id)
        if run is None:
            raise InputValidationError(f"Scan run {scan_run_id} was not found.")
        if run.mode != AssessmentMode.AUTHENTICATED_COVERAGE.value:
            raise InputValidationError("Only authenticated coverage runs collect three contexts.")
        stage = self._stage(session, run.id, ScanStageName.COLLECT.value)
        if stage is None:
            raise RuntimeError("Missing persisted stage 'collect'.")
        stage.status = "running"
        stage.attempt += 1
        stage.started_at = datetime.now(timezone.utc)
        run.current_stage = ScanStageName.COLLECT.value
        session.flush()

        summaries: list[ContextCollectionSummary] = []
        failed_kinds: set[str] = set()
        contexts = list(
            session.scalars(
                select(ScanContext)
                .where(ScanContext.scan_run_id == run.id)
                .order_by(ScanContext.id)
            )
        )
        for context in contexts:
            if context.status == "failed":
                failed_kinds.add(context.kind)
                continue
            try:
                with session.begin_nested():
                    summaries.append(self.context_collection_service.collect(session, run, context))
            except Exception:  # noqa: BLE001 - context boundary persists only a safe diagnostic
                context = session.get(ScanContext, context.id)
                context.status = "failed"
                context.collection_status = "failed"
                context.completeness = CompletenessStatus.INCOMPLETE.value
                context.failure_count += 1
                context.error_code = "context_collection_failed"
                context.error_message = "Collection failed for this context."
                context.finished_at = datetime.now(timezone.utc)
                failed_kinds.add(context.kind)

        if failed_kinds:
            run.status = ScanRunStatus.INCOMPLETE.value
            if failed_kinds == {ContextKind.USER.value}:
                run.completeness = CompletenessStatus.MISSING_USER_CONTEXT.value
            elif failed_kinds == {ContextKind.ADMIN.value}:
                run.completeness = CompletenessStatus.MISSING_ADMIN_CONTEXT.value
            else:
                run.completeness = CompletenessStatus.INCOMPLETE.value
            run.error_code = "context_collection_incomplete"
            run.error_message = "One or more contexts could not be collected completely."
            stage.status = "completed_with_warnings"
            stage.summary = (
                f"Collected {len(summaries)} context(s); "
                f"{len(failed_kinds)} context(s) were incomplete."
            )
        else:
            stage.status = "completed"
            stage.summary = f"Collected {len(summaries)} context(s)."
            run.progress = PROGRESS_AFTER_STAGE[ScanStageName.COLLECT.value]
        stage.finished_at = datetime.now(timezone.utc)
        session.commit()
        return summaries

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
            self._run_stage(
                session,
                run,
                ScanStageName.COLLECT.value,
                deadline,
                lambda: self._discover_and_collect(session, run, options, deadline),
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
            self._check_interrupted(session, run.id)
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
            self._finalize_quick_context(session, run)
            session.commit()
        except ScanInterrupted:
            session.rollback()
            return
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
        self._check_interrupted(session, run.id)
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
        self._check_interrupted(session, run.id)
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
        self._check_interrupted(session, run.id)
        result = self.gateway.fetch(run.normalized_url, options)
        self._check_interrupted(session, run.id)
        self._persist_fetch(session, run, result, depth=0)
        return result, (
            f"Fetched entry URL with HTTP {result.status_code}; "
            f"discovered {len(result.discovered_urls)} linked URL(s)."
        )

    def _discover_and_collect(
        self,
        session: Session,
        run: ScanRun,
        options: ScanOptions,
        deadline: float,
    ):
        entry, discovery_summary = self._discover(session, run, options)
        result, collection_summary = self._collect(session, run, entry, options, deadline)
        return result, f"{discovery_summary} {collection_summary}"

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
            self._check_interrupted(session, run.id)
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
                self._check_interrupted(session, run.id)
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
        context = session.scalar(
            select(ScanContext).where(
                ScanContext.scan_run_id == run.id,
                ScanContext.kind == ContextKind.ANONYMOUS.value,
            )
        )
        if context is None:
            raise RuntimeError("Missing persisted anonymous quick-scan context.")
        identity_key = canonical_asset_identity("GET", result.final_url)
        asset = session.scalar(
            select(ScanAsset).where(
                ScanAsset.scan_run_id == run.id,
                ScanAsset.context_id == context.id,
                ScanAsset.identity_key == identity_key,
            )
        )
        now = datetime.now(timezone.utc)
        attributes = {
            "content_type": result.content_type,
            "body_size_bytes": result.body_size_bytes,
            "body_is_text": result.body_is_text,
            "elapsed_ms": result.elapsed_ms,
            "attempts": result.attempts,
            "depth": depth,
            "redirects": result.redirects,
            "discovered_urls": result.discovered_urls,
        }
        if asset is None:
            asset = ScanAsset(
                scan_run_id=run.id,
                context_id=context.id,
                identity_key=identity_key,
                asset_type="page",
                url=redacted_observed_url(result.final_url),
                method="GET",
                status_code=result.status_code,
                title=result.title,
                attributes=attributes,
                discovered_at=now,
            )
            session.add(asset)
            session.flush()
        headers = self.redaction_service.redact_mapping(result.headers)
        if result.body_is_text:
            preview = self.redaction_service.redact_text(result.body_text, limit=1200)
        else:
            preview = (
                "[non-text response omitted; "
                f"content-type={result.content_type or 'unknown'}; "
                f"size={result.body_size_bytes} bytes]"
            )
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

    def _finalize_quick_context(self, session: Session, run: ScanRun) -> None:
        if run.mode != AssessmentMode.QUICK.value:
            return
        context = session.scalar(
            select(ScanContext).where(
                ScanContext.scan_run_id == run.id,
                ScanContext.kind == ContextKind.ANONYMOUS.value,
            )
        )
        if context is None:
            raise RuntimeError("Missing persisted anonymous quick-scan context.")
        context.status = run.status
        context.login_status = "not_applicable"
        context.session_validation_status = "not_applicable"
        context.collection_status = "completed"
        context.completeness = CompletenessStatus.LEGACY_SINGLE_CONTEXT.value
        context.asset_count = len(run.assets)
        context.request_count = len(run.requests)
        context.failure_count = len(run.failures)
        context.started_at = context.started_at or run.started_at
        context.finished_at = run.finished_at

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

    def _check_interrupted(self, session: Session, scan_run_id: int) -> None:
        status = session.scalar(
            select(ScanRun.status).where(ScanRun.id == scan_run_id),
            execution_options={"autoflush": False},
        )
        if status == ScanRunStatus.INTERRUPTED.value:
            raise ScanInterrupted("Scan was cancelled.")

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
