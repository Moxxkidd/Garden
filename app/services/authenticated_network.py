"""Network admission for authenticated browser and HTTP activity."""

from __future__ import annotations

from urllib.parse import urlparse

from app.core.errors import TargetPolicyError
from app.services.scan_network import TargetNetworkPolicy


class AuthenticatedNetworkGuard:
    """Enforce one authorized origin and the shared destination policy."""

    def __init__(self, policy: TargetNetworkPolicy) -> None:
        self.policy = policy

    def ensure_allowed(self, base_url: str, candidate_url: str) -> str:
        normalized_base = self.policy.normalize_url(base_url)
        normalized_candidate = self.policy.normalize_url(candidate_url)
        if self._origin(normalized_candidate) != self._origin(normalized_base):
            raise TargetPolicyError(
                "Authenticated request left the configured same-origin boundary."
            )
        self.policy.ensure_destination_allowed(normalized_candidate)
        return normalized_candidate

    def _origin(self, value: str) -> tuple[str, str, int]:
        parsed = urlparse(value)
        return (
            parsed.scheme.lower(),
            (parsed.hostname or "").lower(),
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
