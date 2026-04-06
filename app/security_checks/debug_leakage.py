"""Debug, error, and diagnostic leakage indicators."""

from __future__ import annotations

import re

from app.security_checks.base import CheckContext, CheckFindingCandidate, CheckMetadata

_PATTERNS = (
    (re.compile(r"traceback", re.IGNORECASE), "stack trace marker"),
    (re.compile(r"stack trace", re.IGNORECASE), "stack trace marker"),
    (re.compile(r"debug (mode|toolbar|banner)", re.IGNORECASE), "debug banner"),
    (re.compile(r"\bexception\b", re.IGNORECASE), "exception marker"),
)


class DebugLeakageCheck:
    metadata = CheckMetadata(
        name="debug_leakage",
        purpose="Flag low-risk debug, stack trace, and diagnostic indicators after login.",
        category="debug_error_leakage",
        severity="medium",
        confidence="medium",
        trigger_explanation=(
            "Triggers when authenticated pages or endpoints expose stack traces, debug banners, "
            "exception markers, or actuator-like diagnostics."
        ),
        false_positive_boundaries=(
            "Marker-only signal. It does not prove exploitability or uncontrolled access; some "
            "staging and support pages intentionally expose diagnostics to trusted users."
        ),
        remediation_notes=(
            "Disable debug views in authenticated environments, suppress stack traces, and "
            "restrict actuator-style diagnostics to tightly controlled admin channels."
        ),
    )

    def run(self, context: CheckContext) -> list[CheckFindingCandidate]:
        matches: list[str] = []
        refs: list[dict[str, object]] = []

        for page in context.pages:
            haystack = " ".join(
                part for part in [page.title or "", page.text_preview or ""] if part
            )
            for pattern, label in _PATTERNS:
                if pattern.search(haystack):
                    matches.append(f"page {page.url} exposes {label}")
                    refs.append({"kind": "page", "id": page.id, "url": page.url})
                    break

        for endpoint in context.endpoints:
            preview = endpoint.response_preview or ""
            endpoint_label = f"{endpoint.method} {endpoint.path}"
            matched = False
            for pattern, label in _PATTERNS:
                if pattern.search(preview):
                    matches.append(f"endpoint {endpoint_label} exposes {label}")
                    refs.append(
                        {
                            "kind": "endpoint",
                            "id": endpoint.id,
                            "method": endpoint.method,
                            "path": endpoint.path,
                        }
                    )
                    matched = True
                    break
            if matched:
                continue
            if "actuator" in endpoint.path.lower():
                matches.append(f"endpoint {endpoint_label} exposes actuator-style diagnostics")
                refs.append(
                    {
                        "kind": "endpoint",
                        "id": endpoint.id,
                        "method": endpoint.method,
                        "path": endpoint.path,
                    }
                )

        if not matches:
            return []

        return [
            CheckFindingCandidate(
                title="Debug or diagnostic artifacts are visible in authenticated responses",
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
