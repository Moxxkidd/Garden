"""Independent passive identity-run orchestration on the shared discovery engine."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin

from sqlalchemy import select

from app.core.errors import InputValidationError, OverallScanTimeout, ScanInterrupted
from app.core.settings import get_settings
from app.db.bootstrap import session_scope
from app.models.auth_session import AuthSession
from app.models.credential_profile import CredentialProfile
from app.models.scan_context import ScanContext
from app.models.scan_request import ScanRequest
from app.models.scan_run import ScanAsset, ScanEvidence, ScanRun, ScanRunStage
from app.models.target import Target
from app.schemas.identity import IdentityCollectionRequest, SessionHealthView
from app.schemas.scan import DiscoveredAsset
from app.services.browser_discovery import BrowserDiscovery
from app.services.coverage_identity import redacted_observed_url
from app.services.discovery import DiscoveryLedger, origin, safe_route
from app.services.discovery_js import extract_js
from app.services.discovery_sources import parse_seed_input, parse_sitemap
from app.services.identity_health import IdentityHealthGate, IdentityHealthStopped
from app.services.identity_sessions import IdentitySessionService
from app.services.scan_application import ThreadedScanDispatcher
from app.services.scan_network import HttpScanGateway, TargetNetworkPolicy
from app.services.scan_options import resolve_scan_options
from app.services.scan_pipeline import ScanPipeline
from app.services.scan_reporting import ScanReportService


class IdentityCollectionService:
    def __init__(self, *, sessions=None, dispatcher=None, report_service=None):
        self.settings = get_settings()
        self.policy = TargetNetworkPolicy(self.settings)
        self.sessions = sessions or IdentitySessionService(policy=self.policy)
        self.storage = self.sessions.storage
        self.dispatcher = dispatcher or ThreadedScanDispatcher(
            self.settings.scan_max_concurrent_tasks
        )
        self.pipeline = ScanPipeline(
            policy=self.policy,
            gateway=HttpScanGateway(self.policy),
            report_service=report_service or ScanReportService(),
        )

    def start(self, session, request: IdentityCollectionRequest, *, recovery=None):
        request = IdentityCollectionRequest.model_validate(request)
        target = session.get(Target, request.target_id)
        if not target or target.status != "active":
            raise InputValidationError("目标不可用于采集。")
        entry = self.policy.normalize_url(request.url)
        if origin(entry) != origin(target.base_url):
            raise InputValidationError("采集入口与目标必须同源。")
        profiles = []
        for profile_id in request.profile_ids:
            profile = session.get(CredentialProfile, profile_id)
            if not profile or profile.target_id != target.id:
                raise InputValidationError("身份不存在或不属于当前目标。")
            profiles.append(profile)
        options = resolve_scan_options(request.options, self.settings)
        if options.seed_input:
            parse_seed_input(options.seed_input, options.seed_format, base_url=entry)
        digest = hashlib.sha256(
            json.dumps(
                {"request": request.model_dump(), "recovery": recovery}, sort_keys=True
            ).encode()
        ).hexdigest()
        existing = session.scalar(select(ScanRun).where(ScanRun.active_key == digest))
        if existing:
            return existing.id
        run = ScanRun(
            mode="identity_collection",
            parent_run_id=recovery["parent_run_id"] if recovery else None,
            recovery_context_id=recovery["context_id"] if recovery else None,
            recovery_checkpoint_version=recovery["checkpoint_version"] if recovery else None,
            target_id=target.id,
            input_url=redacted_observed_url(entry),
            normalized_url=redacted_observed_url(entry),
            active_key=digest,
            status="queued",
            current_stage="queued",
            options=options.model_dump(exclude={"seed_input", "sitemap_url"}),
            completeness="pending",
            active_checks_enabled=False,
        )
        session.add(run)
        session.flush()
        run.discovery_input_ref = self.storage.write_request_payload(
            run.id,
            0,
            {
                "entry_url": entry,
                "recovery_work": recovery["work"] if recovery else None,
                "seed_input": options.seed_input,
                "sitemap_url": options.sitemap_url,
            },
        )
        selected = [(None, "anonymous", "匿名", "anonymous")] if request.include_anonymous else []
        selected.extend((p.id, f"profile:{p.id}", p.name, p.role) for p in profiles)
        for profile_id, key, name, role in selected:
            session.add(
                ScanContext(
                    scan_run_id=run.id,
                    kind="anonymous" if profile_id is None else "identity",
                    context_key=key,
                    credential_profile_id=profile_id,
                    identity_snapshot={"name": name, "role": role},
                    health_status="unknown",
                )
            )
        for index, name in enumerate(
            ["validate", "establish_contexts", "collect", "normalize", "analyze", "report"]
        ):
            session.add(ScanRunStage(scan_run_id=run.id, name=name, position=index + 1))
        from app.models.audit_event import AuditEvent

        session.add(
            AuditEvent(
                event_type="identity_collection",
                status="success",
                target_id=target.id,
                detail_redacted={
                    "run_id": run.id,
                    "profile_ids": request.profile_ids,
                    "include_anonymous": request.include_anonymous,
                    "parent_run_id": run.parent_run_id,
                },
            )
        )
        session.commit()
        try:
            self.dispatcher.submit(run.id, self.execute)
        except Exception:
            run.status, run.error_code, run.active_key = "failed", "dispatch_failed", None
            session.commit()
        return run.id

    def _stage(self, session, run, name, status):
        stage = session.scalar(
            select(ScanRunStage).where(
                ScanRunStage.scan_run_id == run.id, ScanRunStage.name == name
            )
        )
        if stage:
            stage.status = status
            if status == "running":
                stage.started_at = datetime.now(timezone.utc)
                stage.attempt += 1
            else:
                stage.finished_at = datetime.now(timezone.utc)
        run.current_stage = name

    def execute(self, run_id):
        with session_scope() as session:
            run = session.get(ScanRun, run_id)
            if not run or run.status != "queued":
                return
            from app.schemas.scan import ScanOptions

            options = resolve_scan_options(ScanOptions.model_validate(run.options), self.settings)
            deadline = time.monotonic() + options.overall_timeout_seconds
            run.status, run.started_at = "running", datetime.now(timezone.utc)
            session.commit()
            try:
                private = self.storage.read_payload(run.discovery_input_ref)
                entry = private["entry_url"]
                self._stage(session, run, "validate", "running")
                self.policy.ensure_destination_allowed(entry)
                self._stage(session, run, "validate", "completed")
                self._stage(session, run, "establish_contexts", "running")
                for context in run.contexts:
                    self._establish(session, context)
                self._stage(session, run, "establish_contexts", "completed")
                session.commit()
                self._stage(session, run, "collect", "running")
                for context in run.contexts:
                    if context.status != "ready":
                        continue
                    try:
                        self._collect(session, run, context, entry, options, private, deadline)
                    except (ScanInterrupted, OverallScanTimeout):
                        raise
                    except Exception:
                        context.status = context.collection_status = "failed"
                        context.completeness, context.error_code = (
                            "incomplete",
                            "identity_collection_failed",
                        )
                        context.error_message = "此身份采集未完成，已取得的观察保留。"
                        context.finished_at = datetime.now(timezone.utc)
                        session.commit()
                self._stage(session, run, "collect", "completed")
                self._stage(session, run, "normalize", "completed")
                # Only confirmed identity observations may enter analysis.
                self._stage(session, run, "analyze", "completed")
                complete = all(c.completeness == "complete" for c in run.contexts)
                run.completeness = "complete" if complete else "incomplete"
                run.status = "completed" if complete else "completed_with_warnings"
                run.progress = 100
            except Exception as error:
                run.status = "interrupted" if isinstance(error, ScanInterrupted) else "incomplete"
                run.completeness = "incomplete"
                run.error_code = (
                    "overall_timeout"
                    if isinstance(error, OverallScanTimeout)
                    else "identity_run_incomplete"
                )
                run.error_message = "身份采集已停止，已知观察保留；请检查身份健康和覆盖限制。"
                for context in run.contexts:
                    if context.completeness != "complete":
                        context.completeness = "incomplete"
                        context.collection_status = "incomplete"
                for stage in run.stages:
                    if stage.status in {"pending", "running"} and stage.name != "report":
                        stage.status = "skipped"
            finally:
                run.active_key = None
                run.finished_at = datetime.now(timezone.utc)
                self._stage(session, run, "report", "running")
                session.commit()
                try:
                    run.report_path = self.pipeline.report_service.generate(session, run.id)
                    self._stage(session, run, "report", "completed")
                except Exception:
                    self._stage(session, run, "report", "failed")
                    run.status, run.error_code = "failed", "report_failed"
                session.commit()

    def _establish(self, session, context):
        context.started_at = datetime.now(timezone.utc)
        if context.kind == "anonymous":
            context.status, context.health_status = "ready", "ready"
            context.login_status = "not_required"
            context.session_validation_status = "not_required"
            return
        stored = session.scalar(
            select(AuthSession)
            .where(
                AuthSession.credential_profile_id == context.credential_profile_id,
                AuthSession.identity_metadata.is_not(None),
                AuthSession.revoked_at.is_(None),
            )
            .order_by(AuthSession.id.desc())
        )
        try:
            if not stored:
                raise InputValidationError("No restored identity")
            self.sessions.load_for_collection(session, stored.id)
            context.auth_session_id = stored.id
            context.status, context.health_status = "ready", "checking"
            context.login_status = "completed"
            context.session_validation_status = "pending"
        except InputValidationError:
            context.status, context.health_status = "failed", "unknown"
            context.login_status = "failed"
            context.completeness, context.error_code = "incomplete", "identity_not_ready"
            context.error_message = "此身份尚未通过恢复验证，请重新认证后补采。"

    def _collect(self, session, run, context, entry, options, private, deadline):
        context.collection_status = "running"
        ledger = DiscoveryLedger(entry, options.max_candidates)
        ledger.add(DiscoveredAsset(url=entry, asset_type="page", source_kind="entry"))
        state = (
            self.sessions.load_for_collection(session, context.auth_session_id)
            if context.auth_session_id
            else None
        )
        from app.services.identity_recovery import IdentityRecoveryService

        recovery_service = IdentityRecoveryService(storage=self.storage)
        pending_work = {}
        uncertain_work = {}
        observed_work = set()

        def remember(item):
            url, method = item.get("route_url") or item["url"], item.get("method", "GET")
            if (
                method in {"GET", "HEAD"}
                and item.get("auto_visit", True)
                and origin(url) == origin(entry)
                and (url, method) not in observed_work
                and len(pending_work) < options.max_candidates
            ):
                pending_work[(url, method)] = {"url": url, "method": method}

        remember({"url": entry})
        seeds = []
        for item in (
            parse_seed_input(private.get("seed_input", ""), options.seed_format, base_url=entry)
            if private.get("seed_input")
            else []
        ):
            remember(item)
            if ledger.add(item) and item["auto_visit"]:
                seeds.append(item["url"])
        if private.get("recovery_work"):
            pending_work.clear()
            seeds = []
            for item in private["recovery_work"]:
                remember(item)
                if item["url"] != entry:
                    seeds.append(item["url"])
        maps = {}
        warnings = []
        if options.sitemap_enabled and not private.get("recovery_work"):
            url = private.get("sitemap_url") or urljoin(entry, "/sitemap.xml")
            self.sessions.guard.ensure_allowed(entry, url)
            maps[url] = 0
            seeds.append(url)
            remember({"url": url})
            ledger.add(DiscoveredAsset(url=url, asset_type="document", source_kind="sitemap"))

        def guard():
            # Also called from the verifier thread: never share the collector Session.
            with session_scope() as check_session:
                self.pipeline._check_interrupted(check_session, run.id)
                if time.monotonic() >= deadline:
                    raise OverallScanTimeout("Identity collection deadline exceeded.")
                if context.auth_session_id:
                    stored = check_session.get(AuthSession, context.auth_session_id)
                    if not stored or stored.revoked_at:
                        raise IdentityHealthStopped("identity_revoked")

        def persist_batch(observations, assessment):
            for evidence_id, asset_id in observations:
                if assessment in {"confirmed", "auth_diagnostic"}:
                    uncertain_work.pop((evidence_id, asset_id), None)
                evidence = session.get(ScanEvidence, evidence_id) if evidence_id else None
                if evidence:
                    evidence.data = {**evidence.data, "identity_assessment": assessment}
                asset = session.get(ScanAsset, asset_id)
                # A later failed batch must not erase earlier confirmed observations.
                prior = (asset.attributes or {}).get("identity_assessment")
                if prior != "confirmed" or assessment == "confirmed":
                    asset.attributes = {**asset.attributes, "identity_assessment": assessment}
            session.commit()

        def validate(admit):
            session.commit()

            def in_fresh_thread():
                with session_scope() as validation_session:
                    return self.sessions.validate(
                        validation_session, context.auth_session_id, before_request=admit
                    )

            # Playwright sync contexts cannot be nested on the collector event loop.
            with ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(in_fresh_thread).result()

        health_gate = None
        if state:
            payload = self.storage.read_identity_payload(
                session.get(AuthSession, context.auth_session_id).storage_ref
            )
            health_gate = IdentityHealthGate(
                session_id=context.auth_session_id,
                request_budget=options.max_browser_requests,
                validate=validate,
                persist=persist_batch,
                admission=guard,
                login_url=payload["verification"]["login_url"],
            )

        def save(complete=False):
            snapshot = dict(context.identity_snapshot or {})
            snapshot["discovery"] = ledger.snapshot(complete=complete)
            if state:
                snapshot["checkpoint_version"] = recovery_service.save(
                    session, context.id, list(pending_work.values()), list(uncertain_work.values())
                )
            context.identity_snapshot = snapshot
            stats = {}
            candidates = []
            for item_context in run.contexts:
                data = (item_context.identity_snapshot or {}).get("discovery", {})
                for kind, counts in data.get("stats", {}).items():
                    stats[kind] = dict(Counter(stats.get(kind, {})) + Counter(counts))
                candidates.extend(
                    {**c, "context_id": item_context.id, "context": item_context.context_key}
                    for c in data.get("candidates", [])
                )
            run.asset_metadata = {
                "version": 2,
                "stats": stats,
                "candidates": candidates,
                "complete": complete and all(c.completeness == "complete" for c in run.contexts),
                "truncated": ledger.truncated,
            }
            session.commit()

        def candidate(item):
            remember(item)
            ledger.add(item)
            if item.get("route_observed"):
                key = ledger.key(item["url"], item.get("method", "GET"), item.get("route_url"))
                saved = ledger.items.get(key)
                if saved:
                    saved.update(observed=True, reason="route_view_observed")
                    route = safe_route(item.get("route_url"))
                    asset = session.scalar(
                        select(ScanAsset).where(
                            ScanAsset.scan_run_id == run.id,
                            ScanAsset.context_id == context.id,
                            ScanAsset.url == route,
                        )
                    )
                    if not asset:
                        asset = ScanAsset(
                            scan_run_id=run.id,
                            context_id=context.id,
                            identity_key="hash-v1:" + hashlib.sha256(route.encode()).hexdigest(),
                            asset_type="page",
                            method="GET",
                            url=route,
                            status_code=None,
                            discovered_at=datetime.now(timezone.utc),
                            attributes={
                                "route_url": route,
                                "identity_assessment": "identity_uncertain",
                                "discovery": {"version": 1, "sources": saved["sources"]},
                            },
                        )
                        session.add(asset)
                        session.flush()
                    if health_gate:
                        uncertain_work[(None, asset.id)] = {
                            "url": item["route_url"],
                            "method": "GET",
                        }
                        pending_work.pop((item["route_url"], "GET"), None)
                        health_gate.pending.append((None, asset.id))
                    else:
                        asset.attributes = {**asset.attributes, "identity_assessment": "confirmed"}

        def response(result, route):
            key = ledger.key(result.final_url, result.method)
            if key not in ledger.items:
                ledger.add(
                    DiscoveredAsset(
                        url=result.final_url,
                        asset_type="page",
                        method=result.method,
                        source_kind="browser_request",
                    )
                )
            ledger.observed(result.final_url, result.method)
            asset = self.pipeline._persist_fetch(session, run, result, depth=0, context=context)
            item = ledger.items.get(key, {})
            asset.attributes = {
                **asset.attributes,
                "identity_assessment": (
                    asset.attributes.get("identity_assessment", "identity_uncertain")
                    if state
                    else "confirmed"
                ),
                "discovery": {
                    "version": 1,
                    "sources": item.get("sources", []),
                    "source_count": item.get("source_count"),
                },
            }
            ref = self.storage.write_request_payload(
                run.id, context.id, {"url": result.final_url, "method": result.method}
            )
            session.add(
                ScanRequest.from_capture(
                    scan_run_id=run.id,
                    source_context_id=context.id,
                    asset_id=asset.id,
                    method=result.method,
                    raw_url=result.final_url,
                    header_names=[],
                    fingerprint="identity-observed-v1",
                    protected_storage_ref=ref,
                )
            )
            session.flush()
            evidence = session.scalar(
                select(ScanEvidence)
                .where(ScanEvidence.asset_id == asset.id)
                .order_by(ScanEvidence.id.desc())
            )
            if evidence:
                evidence.data = {
                    **evidence.data,
                    "identity_assessment": "identity_uncertain" if state else "confirmed",
                }
            context.request_count += 1
            observed_work.add((result.final_url, result.method))
            pending_work.pop((result.final_url, result.method), None)
            if health_gate:
                uncertain_work[(evidence.id if evidence else None, asset.id)] = {
                    "url": result.final_url,
                    "method": result.method,
                }
            save()
            if health_gate:
                health_gate.observe(result, (evidence.id if evidence else None, asset.id))

        def sources(result):
            found = []
            if result.final_url in maps:
                try:
                    if result.status_code != 200 or result.body_truncated:
                        raise InputValidationError("Sitemap incomplete")
                    urls, indexes = parse_sitemap(
                        result.final_url, result.body_text, limit=options.max_candidates
                    )
                    found.extend(urls)
                    for url in indexes:
                        if url in maps:
                            continue
                        depth = maps[result.final_url] + 1
                        allowed = (
                            depth <= options.max_sitemap_depth
                            and len(maps) < options.max_sitemap_documents
                        )
                        if allowed:
                            maps[url] = depth
                        else:
                            warnings.append("sitemap_limit")
                        found.append(
                            {
                                "url": url,
                                "asset_type": "document",
                                "source_kind": "sitemap",
                                "auto_visit": allowed,
                            }
                        )
                except InputValidationError:
                    warnings.append("sitemap_incomplete")
            if (
                options.js_enabled
                and not result.body_truncated
                and (
                    "javascript" in (result.content_type or "")
                    or result.final_url.split("?", 1)[0].endswith(".js")
                )
            ):
                found.extend(extract_js(result.final_url, result.body_text, options.max_candidates))
            return found

        def attempt(url, method):
            ledger.attempted(url, method)

        try:
            outcome = BrowserDiscovery(self.policy).collect(
                entry,
                options,
                guard,
                response,
                candidate,
                seeds,
                attempt,
                identity_state=state,
                response_candidates=sources,
                before_send=health_gate.before_send if health_gate else None,
                recovery_work=private.get("recovery_work"),
                allowed_urls=(
                    {x["url"] for x in private["recovery_work"]}
                    if private.get("recovery_work")
                    else None
                ),
            )
            if outcome["stopped_reason"]:
                warnings.append(outcome["stopped_reason"])
            if health_gate:
                health_gate.finish()
            healthy = True
            context.asset_count = len(
                session.scalars(select(ScanAsset).where(ScanAsset.context_id == context.id)).all()
            )
            context.status = context.collection_status = "completed" if healthy else "incomplete"
            context.completeness = (
                "complete" if healthy and not warnings and not ledger.truncated else "incomplete"
            )
            context.health_status = "ready" if healthy else "validation_failed"
            context.finished_at = datetime.now(timezone.utc)
            if warnings:
                context.error_code = warnings[0]
            for item in ledger.items.values():
                if item["reason"] == "pending":
                    item["reason"] = outcome["stopped_reason"] or "request_failed"
        except IdentityHealthStopped as error:
            if health_gate and not health_gate.reason_code:
                health_gate.reason_code = str(error)
                health_gate.health = SessionHealthView(
                    session_id=context.auth_session_id, status="unknown", reason_code=str(error)
                )
            context.status = context.collection_status = "incomplete"
            context.completeness = "incomplete"
            context.error_code = health_gate.reason_code or str(error)
            context.error_message = "身份未能继续确认，未确认批次保留为身份不确定。"
        finally:
            if health_gate:
                if health_gate.pending:
                    persist_batch(health_gate.pending, "identity_uncertain")
                    health_gate.pending = []
                context.health_status = health_gate.health.status
                context.health_checked_at = health_gate.health.checked_at
                context.session_validation_status = (
                    "completed" if health_gate.health.status == "ready" else "failed"
                )
                snapshot = dict(context.identity_snapshot or {})
                snapshot["request_attempts"] = health_gate.request_count
                context.identity_snapshot = snapshot
            context.asset_count = len(
                session.scalars(select(ScanAsset).where(ScanAsset.context_id == context.id)).all()
            )
            context.finished_at = datetime.now(timezone.utc)
            save(complete=context.completeness == "complete")
