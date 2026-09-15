"""Resolve submission defaults once for execution and preview."""

from app.core.settings import Settings
from app.schemas.scan import ScanOptions


def resolve_scan_options(options: ScanOptions, settings: Settings) -> ScanOptions:
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
