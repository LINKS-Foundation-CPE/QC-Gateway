"""PassthroughPolicyPlugin parity with the historical concurrency block."""

from types import SimpleNamespace

import pytest

from middleware.plugins.datatypes import JobSubmission, Principal
from middleware.policy.passthrough import PassthroughPolicyPlugin

SETTINGS = SimpleNamespace(MAX_CONCURRENT_SHOTS=2500000, MAX_CONCURRENT_SWEEPS=10)


def _principal():
    return Principal(auth_source="keycloak", uid="u1", username="alice")


class StubLimiter:
    def __init__(self, allowed=True):
        self._allowed = allowed
        self.reserve_calls = []
        self.rollback_calls = []

    def try_reserve(self, username, shots, circuits, job_type):
        self.reserve_calls.append((username, shots, circuits, job_type))
        return SimpleNamespace(
            allowed=self._allowed, shots_after=42, pre_increment_id=("shots", 100)
        )

    def rollback(self, username, pre_increment_id):
        self.rollback_calls.append((username, pre_increment_id))


@pytest.mark.asyncio
async def test_allowed_carries_reservation_for_rollback():
    limiter = StubLimiter(allowed=True)
    plugin = PassthroughPolicyPlugin(SETTINGS, limiter)
    decision = await plugin.check_submission(
        _principal(), JobSubmission(shots=100, circuits=1, job_type="circuit")
    )
    assert decision.allowed
    assert limiter.reserve_calls == [("alice", 100, 1, "circuit")]

    await plugin.rollback(decision)
    assert limiter.rollback_calls == [("alice", ("shots", 100))]


@pytest.mark.asyncio
async def test_denied_shot_message_matches_legacy():
    plugin = PassthroughPolicyPlugin(SETTINGS, StubLimiter(allowed=False))
    decision = await plugin.check_submission(
        _principal(), JobSubmission(shots=100, circuits=1, job_type="circuit")
    )
    assert not decision.allowed
    assert decision.status_code == 429
    assert decision.reason == "Max concurrent shots reached (2500000) for user alice"


@pytest.mark.asyncio
async def test_denied_sweep_message_matches_legacy():
    plugin = PassthroughPolicyPlugin(SETTINGS, StubLimiter(allowed=False))
    decision = await plugin.check_submission(
        _principal(), JobSubmission(shots=None, circuits=0, job_type="sweep")
    )
    assert decision.reason == "Max concurrent sweeps reached (10) for user alice"


@pytest.mark.asyncio
async def test_rollback_without_reservation_is_noop():
    limiter = StubLimiter()
    plugin = PassthroughPolicyPlugin(SETTINGS, limiter)
    from middleware.plugins.datatypes import PolicyDecision

    await plugin.rollback(PolicyDecision(allowed=False))
    assert limiter.rollback_calls == []


@pytest.mark.asyncio
async def test_rollback_returns_the_reserved_capacity():
    """The path the middleware takes when the machine refuses a submission.

    Capacity is taken before the request is proxied, so a machine that says
    no must not leave the user's counters charged for a job that never ran.
    """
    limiter = StubLimiter(allowed=True)
    plugin = PassthroughPolicyPlugin(SETTINGS, limiter)
    decision = await plugin.check_submission(
        _principal(), JobSubmission(shots=100, circuits=1, job_type="circuit")
    )

    await plugin.rollback(decision)

    assert limiter.rollback_calls == [("alice", ("shots", 100))]


@pytest.mark.asyncio
async def test_denied_submission_reserves_nothing_to_roll_back():
    """A refusal must not need undoing: the limiter already rolled itself back."""
    limiter = StubLimiter(allowed=False)
    plugin = PassthroughPolicyPlugin(SETTINGS, limiter)
    decision = await plugin.check_submission(
        _principal(), JobSubmission(shots=100, circuits=1, job_type="circuit")
    )

    assert not decision.allowed
    assert decision.reservation is None
    await plugin.rollback(decision)
    assert limiter.rollback_calls == []
