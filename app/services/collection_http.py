"""HTTP collector consuming the durable frontier, preserving existing scan outputs."""

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from copy import copy

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import (
    ConflictError,
    InputValidationError,
    OverallScanTimeout,
    TargetPolicyError,
)
from app.db.bootstrap import get_engine
from app.models.collection_queue import CollectionGate, CollectionWork
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanRun
from app.schemas.scan import DiscoveredAsset
from app.services.collection_runtime import CollectionRuntime, CollectionStopped
from app.services.discovery import origin
from app.services.discovery_scan import ScanDiscovery
from app.services.scan_network import FetchError


def collect_http(
    pipeline, session, run, options, deadline, *, runtime=None, discovery=None, finalize=True
):
    context = session.scalar(
        select(ScanContext).where(
            ScanContext.scan_run_id == run.id, ScanContext.kind == "anonymous"
        )
    )
    runtime = runtime or CollectionRuntime(pipeline, session, run, context, options, deadline)
    discovery = discovery or ScanDiscovery(
        pipeline, session, run, options, deadline, run.normalized_url
    )
    previous = list(
        session.scalars(
            select(CollectionWork).where(
                CollectionWork.queue_id == runtime.queue_id, CollectionWork.attempts > 0
            )
        )
    )
    page_requests = sum(w.asset_type == "page" for w in previous)
    resource_requests = len(previous) - page_requests
    active_work = None
    fresh = not discovery.ledger.items

    def _enqueue(item, depth):
        accepted = discovery.ledger.add(item)
        if options.collection_mode == "hybrid" and item.source_kind == "hash_route":
            route = item.route_url or item.url
            if route not in discovery.hybrid_routes:
                discovery.hybrid_routes.append(route)
        if (
            not accepted
            or not runtime.accepts(item.route_url or item.url, item.method or "GET")
            or not item.auto_visit
            or origin(item.url) != runtime.queues.row(session, runtime.queue_id).origin
        ):
            return
        work = runtime.queues.enqueue(
            session,
            runtime.queue_id,
            item.route_url or item.url,
            method=item.method or "GET",
            depth=depth,
            source=item.source_kind,
            asset_type=item.asset_type,
            max_depth=options.max_depth,
        )
        if work["reason"]:
            discovery.ledger.items[discovery.ledger.key(item.url, item.method, item.route_url)][
                "reason"
            ] = work["reason"]

    run_id, context_id, run_url = run.id, context.id, run.normalized_url

    def enqueue(item, depth):
        tick = time.perf_counter()
        try:
            return _enqueue(item, depth)
        finally:
            phases["enqueue"] += time.perf_counter() - tick

    lock = threading.RLock()
    workers = options.durable.origin_concurrency
    pending = {}
    waiters = {}
    stop = None
    phases = dict.fromkeys(["request", "parse", "enqueue", "evidence_commit", "snapshot"], 0.0)

    def fetch_work(work, request):
        with Session(get_engine(), expire_on_commit=False) as worker_session:
            local = copy(runtime)
            local.session = worker_session
            local.run = worker_session.get(ScanRun, run_id)
            local.context = worker_session.get(ScanContext, context_id)
            local.work_id = work["id"]
            local.latest_attempt = None
            local.retrying = work["not_before"] > 0
            worker_session.commit()

            def set_wait(reason):
                if reason is None:
                    waiters.pop(work["id"], None)
                else:
                    waiters[work["id"]] = reason
                aggregate = next(
                    (
                        kind
                        for kind in ["cooldown", "rate_limit", "concurrency"]
                        if kind in waiters.values()
                    ),
                    None,
                )
                queue = local.queues.row(worker_session, local.queue_id)
                if queue.telemetry["timing"]["waiting"] != aggregate:
                    local.queues.set_waiting(worker_session, local.queue_id, local.token, aggregate)
                    worker_session.commit()

            def check():
                with lock:
                    local.check()
                    worker_session.commit()

            def before(url):
                while True:
                    with lock:
                        local.check()
                        admitted = local.admission.reserve(
                            worker_session,
                            local.queue_id,
                            local.token,
                            url,
                            method=request["method"],
                            work_id=work["id"],
                            kind="redirect"
                            if url != request["url"]
                            else ("sitemap" if work["asset_type"] == "sitemap" else "http"),
                            is_retry=local.retrying,
                            request_limit=local.request_limit,
                        )
                        worker_session.commit()
                        if admitted["admitted"]:
                            set_wait(None)
                            local.latest_attempt = admitted["id"]
                            local.admission.admit(worker_session, local.latest_attempt, local.token)
                            worker_session.commit()
                            return
                        set_wait(admitted["waiting"])
                    # Do not hold either a write transaction or the coordinator lock while waiting.
                    if admitted["retry_at"] - time.time() >= deadline - pipeline.clock():
                        raise CollectionStopped("backoff_exceeds_deadline")
                    time.sleep(min(0.05, max(0, admitted["retry_at"] - time.time())))

            def response(status, retry_after):
                with lock:
                    local.response(status, retry_after)

            try:
                fetch_options = options.model_copy(update={"retry_attempts": 0})
                if options.collection_mode == "hybrid":
                    fetch_options.request_timeout_seconds = min(
                        options.request_timeout_seconds, max(0.001, deadline - pipeline.clock())
                    )
                result = pipeline.gateway.fetch(
                    request["url"],
                    fetch_options,
                    expected_origin=pipeline._origin(run_url),
                    before_request=check,
                    on_request_attempt=before,
                    on_http_response=response,
                    method=request["method"],
                )
                return result, local.latest_attempt, None
            except FetchError as error:
                return None, local.latest_attempt, error
            finally:
                # Release failed admission/read snapshots before waiting for the writer lock.
                worker_session.rollback()
                with lock:
                    if work["id"] in waiters:
                        set_wait(None)

    def consume(work, request, result, attempt, error):
        nonlocal runtime, active_work
        active_work = work
        runtime.work_id, runtime.latest_attempt = work["id"], attempt
        is_map = work["asset_type"] == "sitemap"
        if error is None:
            try:
                if result.status_code == 429:
                    raise FetchError(
                        "rate_limited", "Rate limited.", url=request["url"], retryable=True
                    )
                tick = time.perf_counter()
                runtime.persist(
                    result,
                    active_work["depth"],
                    "document" if is_map else active_work["asset_type"],
                )
                phases["evidence_commit"] += time.perf_counter() - tick
                if is_map:
                    if result.status_code != 200 or result.body_truncated:
                        raise CollectionStopped("sitemap_incomplete")
                    candidates, indexes = parse_sitemap(
                        result.final_url, result.body_text, limit=options.max_candidates
                    )
                    for candidate in candidates:
                        enqueue(DiscoveredAsset.model_validate(candidate), 0)
                    for child in indexes:
                        if origin(child) == origin(run.normalized_url):
                            runtime.queues.enqueue(
                                session,
                                runtime.queue_id,
                                child,
                                source="sitemap_index",
                                depth=active_work["depth"] + 1,
                                asset_type="sitemap",
                            )
                children = pipeline._discoveries(result)
                if request["url"] == run.normalized_url:
                    discovery.entry_has_script_only = any(
                        c.asset_type == "script" for c in children
                    ) and not any(
                        c.auto_visit
                        and c.asset_type == "page"
                        and origin(c.url) == origin(run.normalized_url)
                        for c in children
                    )
                for child in children:
                    enqueue(child, active_work["depth"] + 1)
                for child in discovery.script_sources(result):
                    enqueue(child, active_work["depth"] + 1)
                runtime.queues.complete(session, runtime.queue_id, active_work["id"], runtime.token)
                discovery.ledger.add(
                    DiscoveredAsset(
                        url=request["url"],
                        asset_type="document" if is_map else active_work["asset_type"],
                        source_kind="sitemap" if is_map else "entry",
                    )
                )
                discovery.ledger.attempted(request["url"])
                discovery.ledger.observed(
                    request["url"], response_url=result.final_url, aliases=result.redirects
                )
                tick = time.perf_counter()
                discovery.save()
                phases["snapshot"] += time.perf_counter() - tick
                tick = time.perf_counter()
                session.commit()
                phases["evidence_commit"] += time.perf_counter() - tick
            except FetchError as failure:
                error = failure
        if error is not None:
            if runtime.latest_attempt:
                runtime.admission.finish(session, runtime.latest_attempt, runtime.token)
            gate = session.get(CollectionGate, runtime.queues.row(session, runtime.queue_id).origin)
            current = session.get(CollectionWork, active_work["id"], populate_existing=True)
            when = (
                max(time.time(), gate.cooldown_until)
                if gate and error.retryable and current.attempts <= options.durable.retry_attempts
                else None
            )
            runtime.queues.stop_work(
                session,
                runtime.queue_id,
                active_work["id"],
                runtime.token,
                error.code,
                retry_at=when,
            )
            session.commit()

    try:
        from app.services.discovery_sources import parse_seed_input, parse_sitemap

        if fresh:
            enqueue(
                DiscoveredAsset(url=run.normalized_url, asset_type="page", source_kind="entry"), 0
            )
            session.commit()
            if discovery.private.get("seed_input"):
                for value in parse_seed_input(
                    discovery.private["seed_input"],
                    options.seed_format,
                    base_url=run.normalized_url,
                ):
                    enqueue(DiscoveredAsset.model_validate(value), 0)
        sitemap_requests = sum(w.asset_type == "sitemap" for w in previous)
        session.commit()
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="garden-http") as executor:
            while True:
                with lock:
                    try:
                        runtime.check()
                        session.commit()
                        while stop is None and len(pending) < workers:
                            active_work = runtime.queues.claim(
                                session, runtime.queue_id, runtime.token
                            )
                            session.commit()
                            if active_work is None:
                                break
                            is_map = active_work["asset_type"] == "sitemap"
                            is_page = active_work["asset_type"] == "page"
                            if is_map and (
                                sitemap_requests >= options.max_sitemap_documents
                                or active_work["depth"] > options.max_sitemap_depth
                            ):
                                raise CollectionStopped("max_sitemap_documents")
                            if (is_page and page_requests >= options.max_pages) or (
                                not is_page and resource_requests >= options.max_resources
                            ):
                                if options.discovery_strategy != "diversity":
                                    raise CollectionStopped(
                                        "max_pages" if is_page else "max_resources"
                                    )
                                runtime.queues.stop_work(
                                    session,
                                    runtime.queue_id,
                                    active_work["id"],
                                    runtime.token,
                                    "max_pages" if is_page else "max_resources",
                                )
                                session.commit()
                                continue
                            sitemap_requests += int(is_map)
                            page_requests += int(is_page)
                            resource_requests += int(not is_page)
                            request = runtime.queues.request(
                                session, runtime.queue_id, active_work["id"]
                            )
                            session.commit()
                            future = executor.submit(
                                copy_context().run, fetch_work, active_work, request
                            )
                            pending[future] = (active_work, request)
                    except (
                        CollectionStopped,
                        ConflictError,
                        OverallScanTimeout,
                        InputValidationError,
                        TargetPolicyError,
                    ) as error:
                        session.rollback()
                        stop = error
                if not pending:
                    if stop is not None:
                        raise stop
                    with lock:
                        pending_time = session.scalar(
                            select(CollectionWork.not_before)
                            .where(
                                CollectionWork.queue_id == runtime.queue_id,
                                CollectionWork.state == "deferred",
                            )
                            .order_by(CollectionWork.not_before)
                            .limit(1)
                        )
                        session.commit()
                    if pending_time:
                        runtime.wait_until(pending_time)
                        continue
                    break
                done, _ = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
                for future in done:
                    work, request = pending.pop(future)
                    try:
                        result, attempt, error = future.result()
                        with lock:
                            consume(work, request, result, attempt, error)
                    except (
                        CollectionStopped,
                        ConflictError,
                        OverallScanTimeout,
                        InputValidationError,
                        TargetPolicyError,
                    ) as error:
                        session.rollback()
                        stop = error
    except (
        CollectionStopped,
        ConflictError,
        OverallScanTimeout,
        InputValidationError,
        TargetPolicyError,
    ) as error:
        session.rollback()
        runtime.check_ownership()
        # Safe static codes only; network exceptions containing URLs are never projected.
        reason = (
            str(error)
            if isinstance(error, (CollectionStopped, ConflictError))
            else "collection_stopped"
        )
        if reason != "collection_phase_budget":
            discovery.warning(reason)
        session.commit()
    finally:
        queue = runtime.queues.row(session, runtime.queue_id)
        phases.update(pipeline.gateway.task_timings())
        queue.telemetry = {**queue.telemetry, "phase_seconds": phases}
        session.flush()
        if not finalize:
            session.execute(
                update(CollectionWork)
                .where(
                    CollectionWork.queue_id == runtime.queue_id, CollectionWork.state == "leased"
                )
                .values(state="pending", reason="browser_reserved")
            )
            discovery.save(force=True)
            session.commit()
        else:
            runtime.close()
            status = runtime.queues.get(session, runtime.queue_id)
            if not status["coverage_complete"]:
                discovery.warning("持久队列存在未覆盖工作")
            tick = time.perf_counter()
            if options.discovery_strategy == "diversity":
                discovery.sync_queue(runtime)
            discovery.save(complete=status["coverage_complete"], force=True)
            phases["snapshot"] += time.perf_counter() - tick
            queue = runtime.queues.row(session, runtime.queue_id)
            queue.telemetry = {**queue.telemetry, "phase_seconds": phases}
            session.commit()
    return None, "Durable HTTP collection ended; consult the persisted queue for remaining work."
