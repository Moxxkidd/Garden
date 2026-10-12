"""HTTP collector consuming the durable frontier, preserving existing scan outputs."""

import time

from sqlalchemy import select

from app.core.errors import (
    ConflictError,
    InputValidationError,
    OverallScanTimeout,
    TargetPolicyError,
)
from app.models.collection_queue import CollectionGate, CollectionWork
from app.models.scan_context import ScanContext
from app.schemas.scan import DiscoveredAsset
from app.services.collection_runtime import CollectionRuntime, CollectionStopped
from app.services.discovery import origin
from app.services.discovery_scan import ScanDiscovery
from app.services.scan_network import FetchError


def collect_http(pipeline, session, run, options, deadline):
    context = session.scalar(
        select(ScanContext).where(
            ScanContext.scan_run_id == run.id, ScanContext.kind == "anonymous"
        )
    )
    runtime = CollectionRuntime(pipeline, session, run, context, options, deadline)
    discovery = ScanDiscovery(pipeline, session, run, options, deadline, run.normalized_url)
    page_requests = resource_requests = 0
    active_work = None

    def enqueue(item, depth):
        accepted = discovery.ledger.add(item)
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
        )
        if depth > options.max_depth and work["id"]:
            row = session.get(CollectionWork, work["id"])
            if row.state == "pending":
                row.state, row.reason = "suppressed", "max_depth"

    try:
        enqueue(DiscoveredAsset(url=run.normalized_url, asset_type="page", source_kind="entry"), 0)
        session.commit()
        from app.services.discovery_sources import parse_seed_input, parse_sitemap

        if discovery.private.get("seed_input"):
            for value in parse_seed_input(
                discovery.private["seed_input"], options.seed_format, base_url=run.normalized_url
            ):
                enqueue(DiscoveredAsset.model_validate(value), 0)
        sitemap_requests = 0
        session.commit()
        while True:
            runtime.check()
            active_work = runtime.queues.claim(session, runtime.queue_id, runtime.token)
            session.commit()
            if active_work is None:
                pending_time = session.scalar(
                    select(CollectionWork.not_before)
                    .where(
                        CollectionWork.queue_id == runtime.queue_id,
                        CollectionWork.state == "deferred",
                    )
                    .order_by(CollectionWork.not_before)
                    .limit(1)
                )
                if pending_time:
                    session.commit()
                    runtime.wait_until(pending_time)
                    continue
                break
            runtime.work_id = active_work["id"]
            request = runtime.queues.request(session, runtime.queue_id, active_work["id"])
            is_map = active_work["asset_type"] == "sitemap"
            is_page = active_work["asset_type"] == "page"
            if is_map and (
                sitemap_requests >= options.max_sitemap_documents
                or active_work["depth"] > options.max_sitemap_depth
            ):
                raise CollectionStopped("max_sitemap_documents")
            sitemap_requests += int(is_map)
            if (is_page and page_requests >= options.max_pages) or (
                not is_page and resource_requests >= options.max_resources
            ):
                raise CollectionStopped("max_pages" if is_page else "max_resources")
            page_requests += int(is_page)
            resource_requests += int(not is_page)
            runtime.latest_attempt = None
            try:
                result = pipeline.gateway.fetch(
                    request["url"],
                    options.model_copy(update={"retry_attempts": 0}),
                    expected_origin=pipeline._origin(run.normalized_url),
                    before_request=runtime.check,
                    on_request_attempt=lambda url, request=request, is_map=is_map: (
                        runtime.before_send(
                            url,
                            request["method"],
                            kind="redirect"
                            if url != request["url"]
                            else ("sitemap" if is_map else "http"),
                        )
                    ),
                    method=request["method"],
                    on_http_response=runtime.response,
                )
                if result.status_code == 429:
                    raise FetchError(
                        "rate_limited", "Rate limited.", url=request["url"], retryable=True
                    )
                runtime.persist(
                    result,
                    active_work["depth"],
                    "document" if is_map else active_work["asset_type"],
                )
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
                for child in pipeline._discoveries(result):
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
                discovery.save()
                session.commit()
            except FetchError as error:
                if runtime.latest_attempt:
                    runtime.admission.finish(session, runtime.latest_attempt, runtime.token)
                gate = session.get(
                    CollectionGate, runtime.queues.row(session, runtime.queue_id).origin
                )
                current = session.get(CollectionWork, active_work["id"], populate_existing=True)
                when = (
                    max(time.time(), gate.cooldown_until)
                    if gate
                    and error.retryable
                    and current.attempts <= options.durable.retry_attempts
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
        discovery.warning(reason)
        session.commit()
    finally:
        runtime.close()
        status = runtime.queues.get(session, runtime.queue_id)
        if not status["coverage_complete"]:
            discovery.warning("持久队列存在未覆盖工作")
        discovery.save(complete=status["coverage_complete"])
        session.commit()
    return None, "Durable HTTP collection ended; consult the persisted queue for remaining work."
