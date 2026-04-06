"""Low-risk IDOR-style indicator checks."""

from __future__ import annotations

from app.security_checks.base import CheckContext, CheckFindingCandidate, CheckMetadata

_SUSPICIOUS_PARAMETER_NAMES = {"userid", "accountid", "recordid", "id", "role", "session", "token"}
_SENSITIVE_PATH_MARKERS = ("admin", "manage", "config", "export")


class IdorIndicatorsCheck:
    metadata = CheckMetadata(
        name="idor_indicators",
        purpose="Flag identifier-driven access patterns that merit manual authorization review.",
        category="idor_indicators",
        severity="medium",
        confidence="low",
        trigger_explanation=(
            "Triggers when authenticated endpoints combine sensitive path shapes with identifier "
            "parameters such as userId, role, token, or session."
        ),
        false_positive_boundaries=(
            "Marker-only signal. It does not attempt cross-user access or exploit authorization; "
            "manual review is still required to determine whether an IDOR exists."
        ),
        remediation_notes=(
            "Review authorization checks on identifier-driven endpoints, especially where role, "
            "session, or user identifiers influence returned resource scope."
        ),
    )

    def run(self, context: CheckContext) -> list[CheckFindingCandidate]:
        endpoint_map = {endpoint.id: endpoint for endpoint in context.endpoints}
        matches: list[str] = []
        refs: list[dict[str, object]] = []

        for parameter in context.parameters:
            endpoint = endpoint_map.get(parameter.inventory_endpoint_id or -1)
            if endpoint is None:
                continue
            if parameter.name.lower() not in _SUSPICIOUS_PARAMETER_NAMES:
                continue
            if not any(marker in endpoint.path.lower() for marker in _SENSITIVE_PATH_MARKERS):
                continue
            matches.append(
                "endpoint "
                f"{endpoint.method} {endpoint.path} "
                f"accepts {parameter.source_type}:{parameter.name}"
            )
            refs.append(
                {
                    "kind": "parameter",
                    "id": parameter.id,
                    "name": parameter.name,
                    "source_type": parameter.source_type,
                }
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
                title="Identifier-driven authenticated endpoints merit IDOR review",
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
