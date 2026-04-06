"""Administrative surface exposure indicators."""

from __future__ import annotations

from app.security_checks.base import CheckContext, CheckFindingCandidate, CheckMetadata

_MARKERS = {"admin", "manage", "config", "actuator", "debug"}


class AdministrativeSurfaceCheck:
    metadata = CheckMetadata(
        name="administrative_surface",
        purpose="Summarize authenticated administrative surface indicators for review.",
        category="administrative_surface",
        severity="low",
        confidence="high",
        trigger_explanation=(
            "Triggers when authenticated page titles, URLs, or endpoint paths suggest admin, "
            "manage, config, debug, or actuator surfaces."
        ),
        false_positive_boundaries=(
            "This does not prove insecure access. Some admin or support surfaces are expected and "
            "appropriately restricted; the check highlights coverage points for review."
        ),
        remediation_notes=(
            "Review whether these surfaces are expected for the tested role, ensure strong access "
            "controls, and confirm they are monitored and intentionally exposed."
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
                title="Administrative or diagnostic surfaces are reachable after login",
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
