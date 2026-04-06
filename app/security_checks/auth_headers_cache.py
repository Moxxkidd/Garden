"""Authenticated cache and cookie posture indicators."""

from __future__ import annotations

from app.security_checks.base import CheckContext, CheckFindingCandidate, CheckMetadata

_SENSITIVE_MARKERS = ("admin", "manage", "config", "actuator", "export", "import", "upload")
_SAFE_CACHE_TOKENS = ("no-store", "private", "max-age=0")


class AuthenticatedHeadersCacheCheck:
    metadata = CheckMetadata(
        name="authenticated_headers_cache",
        purpose="Flag weak cache and cookie header posture in authenticated contexts.",
        category="authenticated_headers_cache",
        severity="low",
        confidence="medium",
        trigger_explanation=(
            "Triggers when sensitive authenticated pages appear cacheable or when authenticated "
            "responses set cookies without common safety attributes."
        ),
        false_positive_boundaries=(
            "Some internal dashboards intentionally cache non-sensitive fragments or use "
            "environment-specific cookie behavior in local demo environments."
        ),
        remediation_notes=(
            "Mark sensitive authenticated responses as private/no-store and ensure relevant "
            "cookies use HttpOnly, Secure, and SameSite where deployment context allows."
        ),
    )

    def run(self, context: CheckContext) -> list[CheckFindingCandidate]:
        matches: list[str] = []
        refs: list[dict[str, object]] = []

        for page in context.pages:
            label = f"{page.title or 'page'} ({page.url})"
            if not _looks_sensitive(page.url, page.title):
                continue
            cache_control = (page.cache_control or "").lower()
            if not cache_control or not any(token in cache_control for token in _SAFE_CACHE_TOKENS):
                matches.append(
                    f"sensitive page {label} has cache-control '{page.cache_control or '-'}'"
                )
                refs.append({"kind": "page", "id": page.id, "url": page.url})

        for endpoint in context.endpoints:
            if endpoint.cookie_issue_flags:
                issue_list = ", ".join(endpoint.cookie_issue_flags)
                matches.append(
                    "endpoint "
                    f"{endpoint.method} {endpoint.path} "
                    f"sets cookies with issues: {issue_list}"
                )
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
                title="Authenticated responses show weak cache or cookie header posture",
                inventory_refs=_unique_refs(refs),
                reproduction_notes="\n".join(f"- {match}" for match in sorted(set(matches))),
            )
        ]


def _looks_sensitive(url: str, title: str | None) -> bool:
    haystack = f"{url} {title or ''}".lower()
    return any(marker in haystack for marker in _SENSITIVE_MARKERS)


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
