import pytest

from app.core.errors import TargetPolicyError
from app.core.guardrails import ensure_target_allowed
from app.core.settings import Settings


def test_localhost_target_allowed_by_default() -> None:
    settings = Settings()
    ensure_target_allowed("http://localhost:8000", settings)


def test_non_local_target_blocked_by_default() -> None:
    settings = Settings()
    with pytest.raises(TargetPolicyError):
        ensure_target_allowed("https://example.com", settings)
