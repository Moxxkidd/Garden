"""Shared classification for persisted quick-scan diagnostic records."""

from __future__ import annotations


_COVERAGE_WARNING_KEYS = frozenset(
    {
        ("collect", "coverage_limit_reached"),
        ("collect", "cross_origin_redirect_blocked"),
        ("collect", "overall_timeout"),
    }
)


def is_coverage_warning(stage: str, code: str) -> bool:
    """Return whether a diagnostic record describes bounded partial coverage."""

    return (stage, code) in _COVERAGE_WARNING_KEYS
