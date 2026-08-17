"""Quick URL-to-report execution mapped onto the unified assessment stages."""

from __future__ import annotations

import re
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

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
from app.schemas.scan import (
    DiscoveredAsset,
    FetchResult,
    ScanOptions,
    ScanRunStatus,
    ScanStageName,
)
from app.services.context_collection import (
    ContextCollectionService,
    ContextCollectionSummary,
    DefaultContextCollectionGateway,
)
from app.services.context_establishment import ContextEstablishmentService
from app.services.coverage_identity import canonical_asset_identity, redacted_observed_url
from app.services.scan_analysis import PassiveScanAnalyzer
from app.services.scan_failure_classification import is_coverage_warning
from app.services.scan_network import (
    FetchError,
    HttpScanGateway,
    TargetNetworkPolicy,
    classify_asset_type,
)
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
            self._check_deadline(deadline)
            self._run_stage(
                session,
                run,
                ScanStageName.COLLECT.value,
                deadline,
                lambda: self._discover_and_collect(session, run, options, deadline),
                enforce_deadline=False,
            )
            self._run_stage(
                session,
                run,
                ScanStageName.NORMALIZE.value,
                deadline,
                lambda: self._normalize(session, run),
                enforce_deadline=False,
            )
            self._run_stage(
                session,
                run,
                ScanStageName.ANALYZE.value,
                deadline,
                lambda: self._analyze(session, run),
                enforce_deadline=False,
            )
            self._run_stage(
                session,
                run,
                ScanStageName.REPORT.value,
                deadline,
                lambda: self._report(session, run),
                enforce_deadline=False,
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
            session.flush()
            self._finalize_quick_context(session, run)
            session.commit()
            self._try_failure_report(session, run)

    def _run_stage(self, session, run, name, deadline, operation, *, enforce_deadline: bool = True):
        self._check_interrupted(session, run.id)
        if enforce_deadline:
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
        if enforce_deadline:
            self._check_deadline(deadline)
        stage = self._stage(session, run.id, name)
        session.refresh(run)
        stage.status = (
            "completed_with_warnings"
            if any(
                failure.stage == name and is_coverage_warning(failure.stage, failure.code)
                for failure in run.failures
            )
            else "completed"
        )
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

    def _discover(
        self,
        session: Session,
        run: ScanRun,
        options: ScanOptions,
        deadline: float,
    ):
        self._check_interrupted(session, run.id)
        result = self.gateway.fetch(
            run.normalized_url,
            options,
            before_request=lambda: self._guard_network_request(session, run.id, deadline),
        )
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
        entry, discovery_summary = self._discover(session, run, options, deadline)
        try:
            self._check_deadline(deadline)
            result, collection_summary = self._collect(session, run, entry, options, deadline)
        except OverallScanTimeout as error:
            self._record_collection_timeout(session, run, error)
            return None, (
                f"{discovery_summary} Collection deadline was reached; partial assets and "
                "evidence will be finalized locally."
            )
        return result, f"{discovery_summary} {collection_summary}"

    def _collect(
        self,
        session: Session,
        run: ScanRun,
        entry: FetchResult,
        options: ScanOptions,
        deadline: float,
    ):
        page_queue: list[tuple[DiscoveredAsset, int]] = []
        resource_queue: list[tuple[DiscoveredAsset, int]] = []
        seen = {entry.final_url, run.normalized_url}
        requested = {entry.final_url}
        candidates: dict[str, DiscoveredAsset] = {
            entry.final_url: DiscoveredAsset(url=entry.final_url, asset_type="page")
        }
        origin = self._origin(entry.final_url)
        page_count = 1
        page_requests = 1
        resource_count = 0
        resource_requests = 0
        skipped_external: set[str] = set()
        depth_limited: list[DiscoveredAsset] = []

        def enqueue(discovery: DiscoveredAsset, depth: int) -> None:
            if self._origin(discovery.url) != origin:
                skipped_external.add(discovery.url)
                return
            candidates.setdefault(discovery.url, discovery)
            if discovery.url in seen:
                return
            if depth > options.max_depth:
                depth_limited.append(discovery)
                return
            target = page_queue if discovery.asset_type == "page" else resource_queue
            target.append((discovery, depth))

        for discovery in self._discoveries(entry):
            enqueue(discovery, 1)

        while page_queue and page_requests < options.max_pages:
            self._check_interrupted(session, run.id)
            self._check_deadline(deadline)
            discovery, depth = page_queue.pop(0)
            url = discovery.url
            if url in seen:
                continue
            seen.add(url)
            requested.add(url)
            page_requests += 1
            try:
                result = self.gateway.fetch(
                    url,
                    options,
                    expected_origin=origin,
                    before_request=lambda: self._guard_network_request(session, run.id, deadline),
                )
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
                self._check_deadline(deadline)
                continue
            self._persist_fetch(
                session, run, result, depth=depth, asset_type_hint=discovery.asset_type
            )
            page_count += 1
            for child in self._discoveries(result):
                enqueue(child, depth + 1)
            session.commit()
            self._check_deadline(deadline)

        while resource_queue and resource_requests < options.max_resources:
            self._check_interrupted(session, run.id)
            self._check_deadline(deadline)
            discovery, depth = resource_queue.pop(0)
            url = discovery.url
            if url in seen:
                continue
            seen.add(url)
            requested.add(url)
            resource_requests += 1
            try:
                result = self.gateway.fetch(
                    url,
                    options,
                    expected_origin=origin,
                    before_request=lambda: self._guard_network_request(session, run.id, deadline),
                )
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
                self._check_deadline(deadline)
                continue
            self._persist_fetch(
                session, run, result, depth=depth, asset_type_hint=discovery.asset_type
            )
            resource_count += 1
            session.commit()
            self._check_deadline(deadline)

        uncovered = [item for url, item in candidates.items() if url not in requested]
        limits: list[str] = []
        if page_queue:
            limits.append(f"max_pages={options.max_pages}")
        if resource_queue:
            limits.append(f"max_resources={options.max_resources}")
        if depth_limited:
            limits.append(f"max_depth={options.max_depth}")
        if uncovered:
            counts = Counter(item.asset_type for item in uncovered)
            rendered_counts = ", ".join(
                f"{asset_type}：{count}" for asset_type, count in sorted(counts.items())
            )
            samples: list[str] = []
            for asset_type in sorted(counts):
                urls = [item.url for item in uncovered if item.asset_type == asset_type][:3]
                samples.append(f"{asset_type}=[{', '.join(urls)}]")
            message = (
                f"候选 URL 总数：{len(candidates)}；已请求数量：{len(requested)}；"
                f"未覆盖数量：{len(uncovered)}；命中限制：{', '.join(limits) or '未知'}；"
                f"未覆盖类型：{rendered_counts}；代表样本：{'；'.join(samples)}"
            )
            self._record_failure(
                session,
                run,
                stage=ScanStageName.COLLECT.value,
                code="coverage_limit_reached",
                message=message,
                url=None,
                retryable=False,
                attempt=1,
            )
        return None, (
            f"Recorded {page_count} page(s) and {resource_count} resource(s); skipped "
            f"{len(skipped_external)} unique external link(s); {len(uncovered)} URL(s) remained "
            "outside configured bounds."
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
        self,
        session: Session,
        run: ScanRun,
        result: FetchResult,
        *,
        depth: int,
        asset_type_hint: str | None = None,
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
        asset_type = classify_asset_type(
            result.final_url, result.content_type, hint=asset_type_hint
        )
        version_hints = self._version_hints(result.final_url, result.body_text, asset_type)
        security_signals = self._resource_security_signals(asset_type, result.body_text)
        collection_bucket = "page" if asset_type_hint in {None, "page"} else "resource"
        attributes = {
            "content_type": result.content_type,
            "body_size_bytes": result.body_size_bytes,
            "body_is_text": result.body_is_text,
            "elapsed_ms": result.elapsed_ms,
            "attempts": result.attempts,
            "depth": depth,
            "redirects": result.redirects,
            "discovered_urls": result.discovered_urls,
            "body_sha256": result.body_sha256,
            "body_truncated": result.body_truncated,
            "collection_bucket": collection_bucket,
            "version_hints": version_hints,
            "security_signals": security_signals,
        }
        if asset is None:
            asset = ScanAsset(
                scan_run_id=run.id,
                context_id=context.id,
                identity_key=identity_key,
                asset_type=asset_type,
                url=redacted_observed_url(result.final_url),
                method="GET",
                status_code=result.status_code,
                title=result.title,
                attributes=attributes,
                discovered_at=now,
            )
            session.add(asset)
            session.flush()
        headers = self.redaction_service.redact_http_headers(result.headers)
        resource_summary = None
        if asset_type in {"stylesheet", "script", "image", "document"}:
            resource_summary = {
                "size_bytes": result.body_size_bytes,
                "sha256": result.body_sha256,
                "truncated": result.body_truncated,
                "version_hints": version_hints,
                "security_signals": security_signals,
            }
            if result.body_is_text:
                preview = "[resource body preview omitted]"
            else:
                preview = (
                    "[non-text response omitted; "
                    f"content-type={result.content_type or 'unknown'}; "
                    f"size={result.body_size_bytes} bytes]"
                )
        elif result.body_is_text:
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
                data={
                    "headers": headers,
                    "preview": preview,
                    "resource_summary": resource_summary,
                    "collection_bucket": collection_bucket,
                },
                collected_at=now,
            )
        )
        run.retry_count += max(0, result.attempts - 1)
        session.flush()

    def _discoveries(self, result: FetchResult) -> list[DiscoveredAsset]:
        if result.discovered_assets:
            return result.discovered_assets
        return [
            DiscoveredAsset(url=url, asset_type=classify_asset_type(url))
            for url in result.discovered_urls
        ]

    def _version_hints(self, url: str, body: str, asset_type: str) -> list[str]:
        parsed = urlparse(url)
        values: list[str] = []
        version_pattern = r"(?<!\d)(\d+\.\d+(?:\.\d+)?)(?!\d)"
        values.extend(re.findall(version_pattern, parsed.path))
        query = parse_qs(parsed.query)
        for key in ("v", "ver", "version"):
            for candidate in query.get(key, []):
                match = re.fullmatch(version_pattern, candidate.strip())
                if match:
                    values.append(match.group(1))
        if asset_type in {"stylesheet", "script"}:
            context_pattern = (
                r"(?i)(?:\bversion\b|\bver\b|\bv\b|\bjquery\b|\bswiper\b|"
                r"\bslick\b|\bjwplayer\b|\bpdf\.js\b|\blibrary\b|\bwidget\b)"
                r"\s*[:=_-]?\s*" + version_pattern
            )
            values.extend(re.findall(context_pattern, body[:4096]))
        return list(dict.fromkeys(values))[:5]

    def _resource_security_signals(self, asset_type: str, body: str) -> list[str]:
        signals: list[str] = []
        lowered = body.lower()
        if asset_type == "stylesheet":
            if "@import" in lowered:
                signals.append("external_import")
            if "data:" in lowered:
                signals.append("inline_data_uri")
        if asset_type == "script":
            if "sourcemappingurl=" in lowered:
                signals.append("source_map_reference")
            if "eval(" in lowered or "new function(" in lowered:
                signals.append("dynamic_code_execution")
            if "document.write(" in lowered:
                signals.append("document_write")
        return signals

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

    def _record_collection_timeout(
        self,
        session: Session,
        run: ScanRun,
        error: OverallScanTimeout,
    ) -> None:
        existing = session.scalar(
            select(ScanFailure.id).where(
                ScanFailure.scan_run_id == run.id,
                ScanFailure.stage == ScanStageName.COLLECT.value,
                ScanFailure.code == "overall_timeout",
            )
        )
        if existing is None:
            self._record_failure(
                session,
                run,
                stage=ScanStageName.COLLECT.value,
                code="overall_timeout",
                message=str(error),
                url=run.normalized_url,
                retryable=False,
                attempt=1,
            )
        session.commit()

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
        failures = list(
            session.scalars(select(ScanFailure).where(ScanFailure.scan_run_id == run.id))
        )
        coverage_warnings = [
            failure for failure in failures if is_coverage_warning(failure.stage, failure.code)
        ]
        request_failures = [
            failure for failure in failures if not is_coverage_warning(failure.stage, failure.code)
        ]
        if run.status == ScanRunStatus.FAILED.value:
            context.collection_status = "failed"
            context.completeness = CompletenessStatus.INCOMPLETE.value
            context.error_code = run.error_code
            context.error_message = run.error_message
        else:
            context.collection_status = (
                "completed_with_warnings" if coverage_warnings else "completed"
            )
            context.completeness = (
                CompletenessStatus.INCOMPLETE.value
                if coverage_warnings
                else CompletenessStatus.LEGACY_SINGLE_CONTEXT.value
            )
        context.asset_count = len(run.assets)
        context.request_count = len(run.requests)
        context.failure_count = len(request_failures)
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
        if self.clock() >= deadline:
            raise OverallScanTimeout("The overall scan timeout was exceeded.")

    def _guard_network_request(
        self,
        session: Session,
        scan_run_id: int,
        deadline: float,
    ) -> None:
        self._check_interrupted(session, scan_run_id)
        self._check_deadline(deadline)

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
