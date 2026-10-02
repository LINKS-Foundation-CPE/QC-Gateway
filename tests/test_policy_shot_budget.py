"""ShotBudgetPolicyPlugin — license-derived budgets and rollback.

The pool-claim path is gone (2026-08-04): SLURM is the only allocator, so a
principal that is not from the cluster gets the flat MAX_CONCURRENT_SHOTS cap
rather than claiming licenses.
"""

from types import SimpleNamespace

import pytest

from middleware.plugins.datatypes import JobSubmission, Principal
from middleware.policy.shot_budget import ShotBudgetPolicyPlugin

CORE = SimpleNamespace(MAX_CONCURRENT_SHOTS=2500000, MAX_CONCURRENT_SWEEPS=10)


def _sqed(n_licenses):
    return Principal(
        auth_source="sqed",
        uid="1000",
        username="sqeduser",
        roles=["cortex_user"],
        metadata={"n_licenses": n_licenses, "job_ids": ["1"]},
    )


def _cloud():
    return Principal(auth_source="keycloak", uid="u1", username="alice")


class StubLimiter:
    def __init__(self, allowed=True):
        self._allowed = allowed
        self.calls = []
        self.rollbacks = []
        self.redis_client = None

    def try_reserve(self, username, shots, circuits, job_type, max_shots_override=None):
        self.calls.append(
            {"username": username, "job_type": job_type, "override": max_shots_override}
        )
        return SimpleNamespace(allowed=self._allowed, shots_after=1, pre_increment_id=("shots", 1))

    def rollback(self, username, pre_increment_id):
        self.rollbacks.append((username, pre_increment_id))


def _plugin(limiter=None, **settings):
    plugin = ShotBudgetPolicyPlugin(CORE, limiter or StubLimiter())
    for key, value in settings.items():
        setattr(plugin._settings, key, value)
    return plugin


async def test_sqed_budget_is_summed_licenses_times_spl_no_claim():
    limiter = StubLimiter()
    plugin = _plugin(limiter, SHOTS_PER_LICENSE=1000)
    decision = await plugin.check_submission(
        _sqed(n_licenses=3), JobSubmission(shots=10, circuits=1, job_type="circuit")
    )
    assert decision.allowed
    assert limiter.calls[0]["override"] == 3000


async def test_budget_is_capped_by_max_concurrent_shots():
    limiter = StubLimiter()
    plugin = _plugin(limiter, SHOTS_PER_LICENSE=2000000)
    await plugin.check_submission(
        _sqed(n_licenses=5), JobSubmission(shots=10, circuits=1, job_type="circuit")
    )
    assert limiter.calls[0]["override"] == CORE.MAX_CONCURRENT_SHOTS


async def test_sqed_zero_licenses_denied():
    decision = await _plugin().check_submission(
        _sqed(n_licenses=0), JobSubmission(shots=10, circuits=1, job_type="circuit")
    )
    assert not decision.allowed
    assert decision.status_code == 429


@pytest.mark.asyncio
async def test_rollback_releases_the_counters():
    limiter = StubLimiter()
    plugin = _plugin(limiter, SHOTS_PER_LICENSE=1000)
    decision = await plugin.check_submission(
        _cloud(), JobSubmission(shots=10, circuits=1, job_type="circuit")
    )
    await plugin.rollback(decision)
    assert limiter.rollbacks == [("alice", ("shots", 1))]


@pytest.mark.asyncio
async def test_sweeps_keep_flat_limit_no_budget():
    limiter = StubLimiter()
    plugin = _plugin(limiter)
    decision = await plugin.check_submission(
        _cloud(), JobSubmission(shots=None, circuits=0, job_type="sweep")
    )
    assert decision.allowed
    assert limiter.calls[0]["override"] is None


@pytest.mark.asyncio
async def test_on_submission_accepted_is_a_noop():
    """Nothing is persisted per submission: the cluster owns the licenses."""
    import fakeredis

    limiter = StubLimiter()
    limiter.redis_client = fakeredis.FakeStrictRedis(decode_responses=True)
    plugin = _plugin(limiter, SHOTS_PER_LICENSE=1000)
    decision = await plugin.check_submission(
        _cloud(), JobSubmission(shots=10, circuits=1, job_type="circuit")
    )
    await plugin.on_submission_accepted(decision, "job-7")
    assert limiter.redis_client.keys("gateway:claim:*") == []


@pytest.mark.asyncio
async def test_non_cluster_principal_gets_the_flat_cap():
    """No claim, no license count: MAX_CONCURRENT_SHOTS is the whole budget."""
    limiter = StubLimiter()
    plugin = _plugin(limiter, SHOTS_PER_LICENSE=1000)
    decision = await plugin.check_submission(
        _cloud(), JobSubmission(shots=10, circuits=1, job_type="circuit")
    )
    assert decision.allowed
    assert limiter.calls[0]["override"] == CORE.MAX_CONCURRENT_SHOTS
