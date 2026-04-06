"""Inventory annotation rules."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from app.models.enums import InventorySubjectType

PATH_MARKERS = [
    "admin",
    "manage",
    "debug",
    "config",
    "export",
    "import",
    "upload",
    "download",
    "actuator",
]

PARAMETER_MARKERS = [
    "userid",
    "role",
    "token",
    "password",
    "key",
    "secret",
    "session",
]


@dataclass(frozen=True)
class AnnotationCandidate:
    subject_type: InventorySubjectType
    subject_ref: str
    marker: str
    reason: str


class InventoryAnnotationService:
    """Build annotation candidates from inventory subjects."""

    def page_annotations(self, page_url: str) -> list[AnnotationCandidate]:
        path = (urlparse(page_url).path or "/").lower()
        return [
            AnnotationCandidate(
                subject_type=InventorySubjectType.PAGE,
                subject_ref=f"page:{urlparse(page_url).path or '/'}",
                marker=marker,
                reason=f"path contains '{marker}'",
            )
            for marker in PATH_MARKERS
            if marker in path
        ]

    def endpoint_annotations(self, method: str, path: str) -> list[AnnotationCandidate]:
        normalized_path = (path or "/").lower()
        return [
            AnnotationCandidate(
                subject_type=InventorySubjectType.ENDPOINT,
                subject_ref=f"endpoint:{method.upper()}:{path or '/'}",
                marker=marker,
                reason=f"path contains '{marker}'",
            )
            for marker in PATH_MARKERS
            if marker in normalized_path
        ]

    def parameter_annotations(self, source_type: str, name: str) -> list[AnnotationCandidate]:
        canonical_name = name.lower()
        return [
            AnnotationCandidate(
                subject_type=InventorySubjectType.PARAMETER,
                subject_ref=f"parameter:{source_type}:{name}",
                marker=marker,
                reason=f"parameter name matches '{marker}'",
            )
            for marker in PARAMETER_MARKERS
            if marker in canonical_name
        ]

    def parameter_is_sensitive(self, name: str) -> bool:
        canonical_name = name.lower()
        return any(marker in canonical_name for marker in PARAMETER_MARKERS)
