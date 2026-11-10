"""ConcurrencyLimiter.max_shots_override behavior (fakeredis)."""

import fakeredis

from middleware.concurrency import ConcurrencyLimiter


def _limiter(max_shots=1000):
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    return ConcurrencyLimiter(client, max_concurrent_shots=max_shots), client


def test_override_below_flat_limit_denies():
    limiter, _ = _limiter(max_shots=1000)
    result = limiter.try_reserve("alice", shots=500, circuits=1, max_shots_override=100)
    assert not result.allowed


def test_override_above_flat_limit_allows():
    limiter, _ = _limiter(max_shots=100)
    result = limiter.try_reserve("alice", shots=500, circuits=1, max_shots_override=1000)
    assert result.allowed


def test_no_override_keeps_flat_limit():
    limiter, _ = _limiter(max_shots=100)
    assert not limiter.try_reserve("alice", shots=500, circuits=1).allowed
    assert limiter.try_reserve("bob", shots=50, circuits=1).allowed


def test_denied_reservation_rolls_counters_back():
    limiter, client = _limiter(max_shots=1000)
    limiter.try_reserve("alice", shots=500, circuits=1, max_shots_override=100)
    assert int(client.get("shots:active:alice") or 0) == 0
    assert int(client.get("jobs:active:alice") or 0) == 0
