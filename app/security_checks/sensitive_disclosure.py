"""Sensitive information disclosure indicators."""

from __future__ import annotations

import re

from app.security_checks.base import CheckContext, CheckFindingCandidate, CheckMetadata

_DISCLOSURE_PATTERNS = (
    (
        re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE),
        "email address",
    ),
    (
        re.compile(
            r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.internal(?:\.[a-z0-9-]+)*\b",
            re.IGNORECASE,
        ),
        "internal hostname",
    ),
    (re.compile(r"(?:/srv/|/opt/|/var/)[^\s\"']+", re.IGNORECASE), "file path"),
    (
        re.compile(r"\b(?:v?\d+\.\d+\.\d+|\d{4}\.\d{2}\.\d{1,2})\b"),
        "version string",
    ),
    (re.compile(r"\btrace[_-]?id\b", re.IGNORECASE), "trace identifier"),
    (
        re.compile(
            r"\b(?:staging|sandbox|non-production|uat|dev)\b",
            re.IGNORECASE,
        ),
        "non-production marker",
    ),
)


class SensitiveDisclosureCheck:
    metadata = CheckMetadata(
        name="sensitive_disclosure",
        purpose="Spot low-risk disclosure indicators in authenticated page and endpoint previews.",
        category="sensitive_information_disclosure",
        severity="medium",
        confidence="medium",
        trigger_explanation=(
            "Triggers when authenticated pages or endpoint previews expose implementation details "
            "such as emails, internal hostnames, file paths, version strings, trace IDs, or "
            "non-production markers."
        ),
        false_positive_boundaries=(
            "Some internal tools legitimately show support contacts, build markers, or environment "
            "labels. Findings indicate review targets rather than confirmed impact."
        ),
        remediation_notes=(
            "Remove unnecessary internal details from authenticated responses, suppress stack and "
            "build metadata in production, and review support diagnostics for least disclosure."
        ),
    )

    def run(self, context: CheckContext) -> list[CheckFindingCandidate]:
        matches: list[str] = []
        refs: list[dict[str, object]] = []

        for page in context.pages:
            preview = page.text_preview or ""
            for pattern, label in _DISCLOSURE_PATTERNS:
                match = pattern.search(preview)
                if not match:
                    continue
                matches.append(f"page {page.url} reveals {label}: {match.group(0)}")
                refs.append({"kind": "page", "id": page.id, "url": page.url})
                break

        for endpoint in context.endpoints:
            preview = endpoint.response_preview or ""
            for pattern, label in _DISCLOSURE_PATTERNS:
                match = pattern.search(preview)
                if not match:
                    continue
                matches.append(
                    f"endpoint {endpoint.method} {endpoint.path} reveals {label}: {match.group(0)}"
                )
                refs.append(
                    {
                        "kind": "endpoint",
                        "id": endpoint.id,
                        "method": endpoint.method,
                        "path": endpoint.path,
                    }
                )
                break

        if not matches:
            return []

        return [
            CheckFindingCandidate(
                title="Sensitive implementation details are disclosed after login",
                inventory_refs=_unique_refs(refs),
                reproduction_notes="\n".join(f"- {match}" for match in sorted(set(matches))),
            )
        ]


def _unique_refs(refs: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[tuple[str, object], ...]] = set()
    unique: list[dict[str, object]] = []
    for ref in refs:
        key = tuple(sorted(ref.items()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique
