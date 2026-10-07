"""Health gates never convert unvalidated observations into identity evidence."""

from types import SimpleNamespace

import pytest

from app.schemas.identity import SessionHealthView
from app.services.identity_health import IdentityHealthGate, IdentityHealthStopped


def gate_fixture(*, budget=100, results=None):
    now = [0.0]
    checks, batches = [], []
    outcomes = iter(results or ["ready"] * 30)

    def validate(admit):
        admit()
        checks.append(now[0])
        return SessionHealthView(status=next(outcomes))

    gate = IdentityHealthGate(
        session_id=7,
        request_budget=budget,
        validate=validate,
        persist=lambda items, assessment: batches.append((list(items), assessment)),
        clock=lambda: now[0],
    )
    return gate, now, checks, batches


def observe(gate, number, status=200, url="https://example.test/asset"):
    gate.before_send()
    gate.observe(SimpleNamespace(status_code=status, final_url=url), number)


def test_health_check_at_twenty_requests_or_thirty_seconds():
    gate, now, checks, batches = gate_fixture()
    for i in range(20):
        observe(gate, i)
    assert len(checks) == 2  # initial + twentieth observed request
    assert batches == [(list(range(20)), "confirmed")]
    now[0] = 31
    observe(gate, 20)
    assert len(checks) == 3
    gate.finish()
    assert len(checks) == 4
    assert gate.request_count == 25  # 21 business + 4 health, no fabricated assets
    assert batches[-1] == ([20], "confirmed")


def test_403_does_not_mean_expired():
    gate, _, checks, batches = gate_fixture()
    observe(gate, 0, status=403)
    assert len(checks) == 2
    assert batches == [([0], "confirmed")]
    assert gate.health.status == "ready"


def test_expiry_retains_confirmed_batch_and_stops_sends():
    gate, _, _, batches = gate_fixture(results=["ready", "ready", "validation_failed"])
    for i in range(20):
        observe(gate, i)
    with pytest.raises(IdentityHealthStopped):
        observe(gate, 20, status=401)
    assert batches == [(list(range(20)), "confirmed"), ([20], "identity_uncertain")]
    count = gate.request_count
    with pytest.raises(IdentityHealthStopped):
        gate.before_send()
    assert gate.request_count == count


def test_budget_never_confirms_unchecked_batch():
    gate, _, _, batches = gate_fixture(budget=2)
    observe(gate, 0)
    with pytest.raises(IdentityHealthStopped):
        gate.finish()
    assert batches == [([0], "identity_uncertain")]
    assert gate.health.status == "unknown"
    assert gate.reason_code == "max_browser_requests"


def test_login_fallback_is_diagnostic_even_when_validation_succeeds():
    gate, _, _, batches = gate_fixture()
    gate.login_url = "https://example.test/login"
    observe(gate, 0, url="https://example.test/login?next=private")
    assert batches == [([0], "auth_diagnostic")]


def test_revocation_during_validation_never_confirms_pending():
    revoked = [False]
    batches = []

    def admission():
        if revoked[0]:
            raise IdentityHealthStopped("identity_revoked")

    def validate(admit):
        admit()
        if gate.started:
            revoked[0] = True
        return SessionHealthView(status="ready")

    gate = IdentityHealthGate(
        session_id=7,
        request_budget=100,
        validate=validate,
        admission=admission,
        persist=lambda items, assessment: batches.append((list(items), assessment)),
    )
    observe(gate, 1)
    with pytest.raises(IdentityHealthStopped):
        gate.finish()
    assert batches == [([1], "identity_uncertain")]
    assert gate.health.status == "unknown"
