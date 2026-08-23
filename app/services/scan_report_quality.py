"""Pure report-quality policy for trusted metadata and finding projection."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlparse

from app.models.scan_run import ScanFinding

_VERSION_PART = r"\d{1,5}"
_VERSION_CORE = rf"{_VERSION_PART}\.{_VERSION_PART}(?:\.{_VERSION_PART})?"
_VERSION = rf"(?<!\d){_VERSION_CORE}(?!\d)"
_VERSION_FULL = re.compile(rf"^{_VERSION}$")
_PATH_VERSION = re.compile(
    rf"(?i)(?P<label>[a-z][a-z0-9]*(?:[._-][a-z][a-z0-9]*)*)"
    rf"[-@_](?P<value>{_VERSION})(?=$|[./_-])"
)
_BODY_VERSION = re.compile(
    rf"(?i)(?:@version|\bversion\b|\bver\b)\s*[:=_-]?\s*v?"
    rf"(?P<value>{_VERSION})"
)
_BODY_COMPONENT_VERSION = re.compile(
    rf"(?i)\b(?P<label>jquery|swiper|slick|jwplayer|pdf\.js)\b\s*"
    rf"(?:version|ver|v)?\s*[:=_-]?\s*(?P<value>{_VERSION})"
)
_VERSION_QUERY_KEYS = ("v", "ver", "version")
_BODY_PREFIX_LIMIT = 4096
_MAX_HINTS = 5


@dataclass(frozen=True)
class VersionHint:
    value: str
    source: Literal["query", "path", "body_marker"]
    detail: str


@dataclass(frozen=True)
class FindingGroup:
    representative: ScanFinding
    observation_count: int
    asset_ids: tuple[int, ...]
    evidence_ids: tuple[int, ...]


def extract_version_hints(url: str, body: str, asset_type: str) -> tuple[VersionHint, ...]:
    parsed = urlparse(url)
    candidates: list[VersionHint] = []

    for match in _PATH_VERSION.finditer(parsed.path):
        candidates.append(VersionHint(match.group("value"), "path", match.group("label").lower()))

    query = parse_qs(parsed.query)
    for key in _VERSION_QUERY_KEYS:
        for raw_value in query.get(key, []):
            value = raw_value.strip()
            if _VERSION_FULL.fullmatch(value):
                candidates.append(VersionHint(value, "query", key))

    if asset_type in {"stylesheet", "script"}:
        prefix = body[:_BODY_PREFIX_LIMIT]
        for match in _BODY_COMPONENT_VERSION.finditer(prefix):
            candidates.append(
                VersionHint(
                    match.group("value"),
                    "body_marker",
                    match.group("label").lower(),
                )
            )
        for match in _BODY_VERSION.finditer(prefix):
            candidates.append(VersionHint(match.group("value"), "body_marker", "version"))

    unique: list[VersionHint] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.value in seen:
            continue
        seen.add(candidate.value)
        unique.append(candidate)
        if len(unique) == _MAX_HINTS:
            break
    return tuple(unique)


def project_finding_groups(findings: Sequence[ScanFinding]) -> tuple[FindingGroup, ...]:
    grouped: dict[tuple[str, ...], list[ScanFinding]] = {}
    for finding in sorted(findings, key=lambda item: item.id):
        key = (
            finding.title,
            finding.category,
            finding.severity,
            finding.confidence,
            finding.summary,
            finding.remediation,
        )
        grouped.setdefault(key, []).append(finding)

    return tuple(
        FindingGroup(
            representative=items[0],
            observation_count=len(items),
            asset_ids=tuple(sorted({value for item in items for value in item.asset_ids})),
            evidence_ids=tuple(sorted({value for item in items for value in item.evidence_ids})),
        )
        for items in grouped.values()
    )
