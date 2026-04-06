"""Upload and import entrypoint indicators."""

from __future__ import annotations

from app.security_checks.base import CheckContext, CheckFindingCandidate, CheckMetadata

_MARKERS = {"upload", "import"}


class UploadImportCheck:
    metadata = CheckMetadata(
        name="upload_import_entrypoints",
        purpose="Highlight upload and import entrypoints without performing dangerous submissions.",
        category="upload_import_entrypoints",
        severity="low",
        confidence="high",
        trigger_explanation=(
            "Triggers when authenticated pages or endpoints expose upload or import markers."
        ),
        false_positive_boundaries=(
            "This is an inventory-style indicator only. It does not submit malicious files or "
            "attempt exploit payloads."
        ),
        remediation_notes=(
            "Review upload/import handling for file validation, content controls, "
            "role-based access, "
            "and audit coverage before deeper testing."
        ),
    )

    def run(self, context: CheckContext) -> list[CheckFindingCandidate]:
        matches: list[str] = []
        refs: list[dict[str, object]] = []

        for annotation in context.annotations:
            if annotation.marker not in _MARKERS:
                continue
            matches.append(
                f"{annotation.subject_type} {annotation.subject_ref} matched '{annotation.marker}'"
            )
            refs.append(
                {
                    "kind": annotation.subject_type,
                    "subject_ref": annotation.subject_ref,
                }
            )

        if not matches:
            return []

        return [
            CheckFindingCandidate(
                title="Upload or import entrypoints are exposed in authenticated flows",
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
