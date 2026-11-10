"""Per-user Redis counters: what admission takes and completion gives back."""

import fakeredis
import pytest

from middleware.concurrency import ConcurrencyLimiter, counter_keys
from middleware.job_counters import decrement_user_counters

USER = "alice@example.org"
SHOTS, JOBS, SWEEPS = counter_keys(USER)


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def limiter(r):
    return ConcurrencyLimiter(r, max_concurrent_shots=10_000, max_concurrent_sweeps=2)


def test_circuit_jobs_do_not_use_up_the_sweep_limit(r, limiter):
    for _ in range(5):
        assert limiter.try_reserve(USER, shots=100, circuits=1).allowed
    assert limiter.try_reserve(USER, job_type="sweep").allowed
    assert r.get(SWEEPS) == "1"
    assert r.get(JOBS) == "6"  # every job still counts towards the total


def test_sweep_limit_counts_sweeps(r, limiter):
    assert limiter.try_reserve(USER, job_type="sweep").allowed
    assert limiter.try_reserve(USER, job_type="sweep").allowed
    refused = limiter.try_reserve(USER, job_type="sweep")
    assert not refused.allowed and refused.limit_exceeded
    assert r.get(SWEEPS) == "2" and r.get(JOBS) == "2"

    decrement_user_counters(r, USER, circuits_count=1, shots_val=0, job_type="sweep")
    assert r.get(SWEEPS) == "1" and r.get(JOBS) == "1"
    assert limiter.try_reserve(USER, job_type="sweep").allowed


def test_a_submission_without_shots_counts_as_a_sweep(r, limiter):
    assert limiter.try_reserve(USER, shots=None).allowed
    assert r.get(SWEEPS) == "1"
    assert r.get(SHOTS) is None


@pytest.mark.parametrize("job_type,shots", [("sweep", None), ("circuit", 20_000)])
def test_a_refused_attempt_does_not_extend_the_counters_life(r, job_type, shots):
    limiter = ConcurrencyLimiter(r, max_concurrent_shots=10_000, max_concurrent_sweeps=2)
    r.set(SWEEPS, 2, ex=100)
    r.set(JOBS, 2, ex=100)
    r.set(SHOTS, 0, ex=100)
    assert not limiter.try_reserve(USER, shots=shots, circuits=1, job_type=job_type).allowed
    for key in (SWEEPS, JOBS, SHOTS):
        assert 0 < r.ttl(key) <= 100, key


def test_an_admitted_submission_refreshes_the_ttl(r, limiter):
    r.set(JOBS, 0, ex=100)
    assert limiter.try_reserve(USER, job_type="sweep").allowed
    assert r.ttl(JOBS) > 100 and r.ttl(SWEEPS) > 100


def test_a_finished_sweep_leaves_the_shot_counter_alone(r, limiter):
    assert limiter.try_reserve(USER, shots=500, circuits=1).allowed
    assert limiter.try_reserve(USER, job_type="sweep").allowed
    decrement_user_counters(r, USER, circuits_count=1, shots_val=0, job_type="sweep")
    assert r.get(SHOTS) == "500"


def test_circuit_job_round_trip(r, limiter):
    assert limiter.try_reserve(USER, shots=100, circuits=3).allowed
    assert r.get(SHOTS) == "300" and r.get(JOBS) == "1"
    decrement_user_counters(r, USER, circuits_count=3, shots_val=100, job_type="circuit")
    assert r.get(SHOTS) == "0" and r.get(JOBS) == "0"
    assert r.get(SWEEPS) is None


def test_rollback_of_a_sweep_releases_both_counters(r, limiter):
    result = limiter.try_reserve(USER, job_type="sweep")
    assert limiter.rollback(USER, result.pre_increment_id)
    assert r.get(SWEEPS) == "0" and r.get(JOBS) == "0"


def test_rollback_of_a_pre_upgrade_reservation_releases_the_job_count_only(r, limiter):
    r.set(JOBS, 1)
    r.set(SWEEPS, 1)
    assert limiter.rollback(USER, ("jobs", 1))
    assert r.get(JOBS) == "0" and r.get(SWEEPS) == "1"


def test_counters_never_go_negative(r):
    # A sweep admitted before the upgrade finishes after it: no sweep count to
    # release, and none is invented.
    r.set(JOBS, 1)
    decrement_user_counters(r, USER, circuits_count=1, shots_val=0, job_type="sweep")
    assert r.get(JOBS) == "0" and r.get(SWEEPS) == "0"
