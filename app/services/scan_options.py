"""Resolve submission defaults once for execution and preview."""

from app.core.settings import Settings
from app.schemas.scan import ScanOptions

DISCOVERY_PRESETS = {
    "quick": {"collection_mode": "http", "sitemap_enabled": False, "js_enabled": False},
    "balanced": {
        "collection_mode": "http",
        "discovery_strategy": "diversity",
        "sitemap_enabled": True,
        "js_enabled": True,
    },
    "deep": {
        "collection_mode": "hybrid",
        "discovery_strategy": "diversity",
        "sitemap_enabled": True,
        "js_enabled": True,
    },
}


def resolve_scan_options(options: ScanOptions, settings: Settings) -> ScanOptions:
    if options.preset is not None:
        options = options.model_copy(
            update={
                key: value
                for key, value in DISCOVERY_PRESETS[options.preset].items()
                if key not in options.model_fields_set
            }
        )
    if options.collection_mode == "hybrid" and options.durable is None:
        from app.schemas.collection_queue import CollectionOptions

        options = options.model_copy(update={"durable": CollectionOptions()})
    return options.model_copy(
        update={
            "request_timeout_seconds": options.request_timeout_seconds
            or settings.scan_request_timeout_seconds,
            "overall_timeout_seconds": options.overall_timeout_seconds
            or settings.scan_overall_timeout_seconds,
            "retry_attempts": options.retry_attempts
            if options.retry_attempts is not None
            else settings.scan_retry_attempts,
        }
    )


HYBRID_BROWSER_PERCENT = 30


def hybrid_browser_budget(options: ScanOptions) -> int:
    return min(
        options.durable.max_requests * HYBRID_BROWSER_PERCENT // 100, options.max_browser_requests
    )
