"""Deterministic path sampling; request identities remain exact and separate."""

import json
import re
from urllib.parse import parse_qsl, urlsplit

from app.services.discovery import origin, safe_route

GROUPS = ("page", "sitemap", "js", "import")


def path_family(url, method="GET"):
    parsed = urlsplit(url)
    path = re.sub(r"/(?:\d{4}-\d{2}-\d{2}|\d+)(?=/|$)", "/{number}", parsed.path)
    return (
        origin(url),
        method.upper(),
        path,
        tuple(sorted(k for k, _ in parse_qsl(parsed.query))),
        safe_route(url),
    )


def display_template_key(url, method):
    """Stable SQL display key; explicit hash routes retain their route context."""
    return json.dumps(
        path_family(url, method or "UNKNOWN"), ensure_ascii=False, separators=(",", ":")
    )


def source_group(source):
    if source in {"sitemap", "sitemap_index"}:
        return "sitemap"
    if source == "js_literal":
        return "js"
    if source in {"url_import", "openapi_import", "recovery", "history"}:
        return "import"
    return "page"


def choose_candidate(candidates, seen, last_group=None):
    """Rows in discovery order: (family, first source, existing priority).

    Priority 0/1 preserves entry and explicit verification order. Otherwise choose
    an unseen family, then rotate the four source groups, then discovery order.
    """
    protected = [i for i, (_, _, priority) in enumerate(candidates) if priority < 2]
    if protected:
        return min(protected, key=lambda i: (candidates[i][2], i)), "required_priority"
    tier = [i for i, (family, _, _) in enumerate(candidates) if family not in seen]
    reason = "new_family" if tier else "sampled_family"
    eligible = tier or list(range(len(candidates)))
    start = (GROUPS.index(last_group) + 1) % len(GROUPS) if last_group in GROUPS else 0
    for offset in range(len(GROUPS)):
        group = GROUPS[(start + offset) % len(GROUPS)]
        for i in eligible:
            if source_group(candidates[i][1]) == group:
                return i, reason
