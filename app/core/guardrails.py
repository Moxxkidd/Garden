"""Central safety guardrails for target handling."""

from ipaddress import ip_address
from urllib.parse import urlparse

from app.core.errors import TargetPolicyError
from app.core.settings import Settings

SAFE_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


def ensure_target_allowed(target: str, settings: Settings) -> None:
    """Conservative Phase 1 guardrail for target admission.

    This intentionally allows only clearly local/demo destinations unless the
    explicit override flag is enabled. Future phases can expand this policy
    with richer target models and audit context.
    """

    if settings.allow_non_local_targets:
        return

    host = _extract_host(target)
    if _is_local_host(host):
        return

    raise TargetPolicyError(
        "Non-local targets are disabled by default. Set "
        "GARDEN_ALLOW_NON_LOCAL_TARGETS=true only for authorized environments."
    )


def _extract_host(target: str) -> str:
    parsed = urlparse(target)
    candidate = parsed.hostname or parsed.path
    return candidate.strip("[]").lower()


def _is_local_host(host: str) -> bool:
    if host in SAFE_LOCAL_HOSTS or host.endswith(".localhost"):
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False
