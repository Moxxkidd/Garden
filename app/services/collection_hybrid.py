"""Anonymous single-entry HTTP/browser collection using one durable lease and budget."""

from sqlalchemy import select

from app.core.errors import ConflictError, OverallScanTimeout
from app.models.scan_context import ScanContext
from app.services.browser_discovery import BrowserUnavailableError
from app.services.collection_browser import CollectionBrowser
from app.services.collection_http import collect_http
from app.services.collection_runtime import CollectionRuntime, CollectionStopped
from app.services.discovery_scan import ScanDiscovery
from app.services.scan_options import hybrid_browser_budget


def collect_hybrid(pipeline, session, run, options, deadline):
    context = session.scalar(
        select(ScanContext).where(
            ScanContext.scan_run_id == run.id, ScanContext.kind == "anonymous"
        )
    )
    runtime = CollectionRuntime(pipeline, session, run, context, options, deadline)
    discovery = ScanDiscovery(pipeline, session, run, options, deadline, run.normalized_url)
    reserved = hybrid_browser_budget(options)
    discovery.hybrid = dict(
        browser_reserved=reserved,
        trigger=None,
        browser_status="not_triggered",
        http_requests=0,
        browser_requests=0,
        http_observations=0,
        browser_observations=0,
    )
    runtime.request_limit = options.durable.max_requests - reserved
    try:
        collect_http(
            pipeline,
            session,
            run,
            options,
            deadline,
            runtime=runtime,
            discovery=discovery,
            finalize=False,
        )
        runtime.check()
        session.commit()
        if discovery.private.get("entry_route"):
            discovery.hybrid_routes.append(discovery.private["entry_route"])
        trigger = (
            "hash_route"
            if discovery.hybrid_routes
            else "script_only_entry"
            if discovery.entry_has_script_only
            else None
        )
        discovery.hybrid["trigger"] = trigger
        discovery.hybrid["http_observations"] = sum(
            i["observed"] for i in discovery.ledger.items.values()
        )
        runtime.request_limit = None
        if trigger is None:
            collect_http(
                pipeline,
                session,
                run,
                options,
                deadline,
                runtime=runtime,
                discovery=discovery,
                finalize=False,
            )
        elif reserved == 0:
            discovery.hybrid["browser_status"] = "no_browser_budget"
            discovery.warning("本轮没有浏览器额度")
        else:
            browser_options = options.model_copy(
                update={
                    "max_browser_requests": reserved,
                    "overall_timeout_seconds": max(0.001, deadline - pipeline.clock()),
                }
            )
            browser = CollectionBrowser(runtime=runtime, start_url=run.normalized_url)
            browser.options = browser_options
            discovery.options = browser_options
            discovery.hybrid["browser_status"] = "bounded"
            discovery.browser_collect(
                runtime=browser, seeds=discovery.hybrid_routes, finalize=False
            )
    except BrowserUnavailableError:
        discovery.hybrid["browser_status"] = "unavailable"
        discovery.warning("浏览器不可用，HTTP 结果已保留；浏览器部分未覆盖")
    except (CollectionStopped, ConflictError, OverallScanTimeout) as error:
        if isinstance(error, ConflictError) and str(error) not in {
            "collection_request_budget",
            "collection_evidence_budget",
            "collection_paused",
        }:
            raise
        discovery.hybrid["browser_status"] = "bounded"
        discovery.warning("混合采集达到预算、截止时间或暂停边界")
    finally:
        runtime.check_ownership()
        metrics = runtime.admission.summary(session, runtime.queue_id)
        discovery.hybrid["browser_requests"] = metrics["by_kind"].get("browser", 0)
        discovery.hybrid["http_requests"] = (
            metrics["requests_used"] - discovery.hybrid["browser_requests"]
        )
        observed = sum(i["observed"] for i in discovery.ledger.items.values())
        if discovery.hybrid["trigger"]:
            discovery.hybrid["browser_observations"] = max(
                0, observed - discovery.hybrid["http_observations"]
            )
        else:
            discovery.hybrid["http_observations"] = observed
        runtime.close()
        status = discovery.sync_queue(runtime)
        if not status["coverage_complete"]:
            discovery.warning("持久队列仍有未覆盖内容")
        discovery.save(complete=status["coverage_complete"], force=True)
        session.commit()
    return None, "Hybrid collection ended; HTTP and browser share the recorded request budget."
