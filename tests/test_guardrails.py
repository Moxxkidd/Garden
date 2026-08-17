import pytest

from app.core.errors import TargetPolicyError
from app.core.guardrails import ensure_target_allowed
from app.core.settings import Settings
from app.services.scan_network import TargetNetworkPolicy


def test_localhost_target_allowed_by_default() -> None:
    settings = Settings()
    ensure_target_allowed("http://localhost:8000", settings)


def test_non_local_target_allowed_by_project_default(monkeypatch) -> None:
    monkeypatch.delenv("GARDEN_ALLOW_NON_LOCAL_TARGETS", raising=False)
    settings = Settings(_env_file=None)

    assert settings.allow_non_local_targets is True
    ensure_target_allowed("https://example.com", settings)


def test_non_local_target_can_be_disabled_explicitly() -> None:
    settings = Settings().model_copy(update={"allow_non_local_targets": False})
    with pytest.raises(TargetPolicyError):
        ensure_target_allowed("https://example.com", settings)


def test_private_target_remains_blocked_by_project_default(monkeypatch) -> None:
    monkeypatch.delenv("GARDEN_ALLOW_NON_LOCAL_TARGETS", raising=False)
    settings = Settings(_env_file=None)
    policy = TargetNetworkPolicy(
        settings,
        resolver=lambda host, port, **kwargs: [(2, 1, 6, "", ("10.0.0.1", port))],
    )

    assert policy.policy_name == "public-and-local"
    with pytest.raises(TargetPolicyError):
        policy.preflight("https://internal.example/")
