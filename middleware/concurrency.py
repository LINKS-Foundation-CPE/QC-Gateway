"""Redis-based concurrency limiting for user submissions.

This module provides abstractions for enforcing per-user concurrency limits
using Redis counters. It handles:
- Incrementing shot/job counters before submission
- Checking against concurrency limits
- Rolling back counters on failure
- Decrementing counters when jobs complete

These functions are decoupled from the middleware and can be used in
different contexts (e.g., standalone scripts, different web frameworks).

Public classes:
- `ConcurrencyLimiter`: manages per-user concurrency limits using Redis

Note for new developers:
- This module uses Redis to track and enforce concurrency limits for user submissions.
- The `ConcurrencyLimiter` class provides methods to reserve, check, and rollback submissions.
- The design ensures that failures (e.g., Redis unavailability) do not block submissions (fail-open).
"""

import logging

import redis

logger = logging.getLogger(__name__)


class ConcurrencyLimiter:
    """Enforces per-user concurrency limits using Redis counters.

    This class encapsulates the Redis counter logic that was previously
    embedded in the middleware. It provides a clean interface for:
    - Pre-incrementing counters before submission
    - Rolling back on failure
    - Checking limits

    Usage:
        limiter = ConcurrencyLimiter(redis_client, max_shots=10000)
        result = limiter.try_reserve(username, shots=100, circuits=5)
        if result.allowed:
            # Proceed with submission
            # ... on failure:
            limiter.rollback(username, result.pre_increment_id)
        else:
            # Reject submission
    """

    def __init__(
        self,
        redis_client: redis.Redis | None,
        max_concurrent_shots: int,
        counter_ttl_seconds: int = 3600,
        max_concurrent_sweeps: int | None = None,
    ):
        """Initialize the concurrency limiter.

        Args:
            redis_client: Redis client instance (can be None, in which case
                         all limit checks pass)
            max_concurrent_shots: Maximum concurrent shots per user
            counter_ttl_seconds: Redis key expiration time (default: 1 hour)
        """
        self.redis_client = redis_client
        self.max_concurrent_shots = max_concurrent_shots
        # Optional per-user concurrent sweep (job) limit used when shots
        # cannot be reliably extracted (e.g., sweep submissions).
        self.max_concurrent_sweeps = max_concurrent_sweeps
        self.counter_ttl_seconds = counter_ttl_seconds

    def try_reserve(
        self,
        username: str,
        shots: int | None = None,
        circuits: int | None = None,
        job_type: str | None = None,
    ) -> "ReservationResult":
        """Try to reserve capacity for a new submission.

        Pre-increments the Redis counters for the user. If the limit is
        exceeded, rolls back and returns allowed=False.

        Args:
            username: Username making the submission
            shots: Number of shots in the submission (default: 1)
            circuits: Number of circuits in the submission (default: 1)

        Returns:
            ReservationResult with allowed status and counters info
        """
        if self.redis_client is None:
            # Redis unavailable, allow all submissions
            return ReservationResult(
                allowed=True, shots_after=shots or 1, jobs_after=1, pre_increment_id=None
            )

        # Determine if this submission should be limited by job count
        # (sweeps) rather than by shot totals. We treat it as a sweep when
        # the job_type is explicitly 'sweep' or when shots is None.
        is_sweep = (job_type == "sweep") or (shots is None)

        # Default values for non-sweep submissions
        shots_val = shots or 1
        circuits_val = circuits or 1

        # Calculate total shot increment (shots x circuits)
        shot_increment = shots_val * circuits_val if shots_val and circuits_val else 1

        shot_counter_key = f"shots:active:{username}"
        job_counter_key = f"jobs:active:{username}"

        try:
            if is_sweep:
                # Only increment job counter and enforce sweep limit when configured
                new_job_count = self.redis_client.incr(job_counter_key)
                self.redis_client.expire(job_counter_key, self.counter_ttl_seconds)

                logger.info(
                    "User %s: jobs=%s (sweep), sweep_limit=%s",
                    username,
                    new_job_count,
                    self.max_concurrent_sweeps,
                )

                if (
                    self.max_concurrent_sweeps is not None
                    and new_job_count > self.max_concurrent_sweeps
                ):
                    # Rollback and deny
                    self.redis_client.decr(job_counter_key)
                    logger.warning(
                        "User %s exceeded concurrent sweep limit (%s > %s)",
                        username,
                        new_job_count,
                        self.max_concurrent_sweeps,
                    )
                    return ReservationResult(
                        allowed=False,
                        shots_after=None,
                        jobs_after=new_job_count - 1,
                        pre_increment_id=None,
                        limit_exceeded=True,
                    )

                return ReservationResult(
                    allowed=True,
                    shots_after=None,
                    jobs_after=new_job_count,
                    pre_increment_id=("jobs", 1),
                )

            # Non-sweep: increment both counters
            new_shot_count = self.redis_client.incrby(shot_counter_key, shot_increment)
            self.redis_client.expire(shot_counter_key, self.counter_ttl_seconds)

            new_job_count = self.redis_client.incr(job_counter_key)
            self.redis_client.expire(job_counter_key, self.counter_ttl_seconds)

            logger.info(
                "User %s: shots=%s, jobs=%s, shot_limit=%s",
                username,
                new_shot_count,
                new_job_count,
                self.max_concurrent_shots,
            )

            # Check if shot limit exceeded
            if new_shot_count > self.max_concurrent_shots:
                # Rollback and deny
                self.redis_client.decrby(shot_counter_key, shot_increment)
                self.redis_client.decr(job_counter_key)

                logger.warning(
                    "User %s exceeded concurrent shot limit (%s > %s)",
                    username,
                    new_shot_count,
                    self.max_concurrent_shots,
                )

                return ReservationResult(
                    allowed=False,
                    shots_after=new_shot_count - shot_increment,
                    jobs_after=new_job_count - 1,
                    pre_increment_id=None,
                    limit_exceeded=True,
                )

            # Success
            return ReservationResult(
                allowed=True,
                shots_after=new_shot_count,
                jobs_after=new_job_count,
                pre_increment_id=("shots", shot_increment),
            )

        except Exception as e:
            logger.exception(f"Redis error during reservation for {username}: {e}")
            # If Redis fails, allow the submission (fail-open)
            return ReservationResult(
                allowed=True,
                shots_after=None,
                jobs_after=None,
                pre_increment_id=None,
                redis_error=str(e),
            )

    def rollback(self, username: str, pre_increment_id: tuple | None = None) -> bool:
        """Rollback a reservation if submission fails.

        Args:
            username: Username to rollback
            pre_increment_id: Return value from try_reserve (contains shot increment)

        Returns:
            True if rollback succeeded, False if Redis unavailable or error
        """
        if self.redis_client is None or pre_increment_id is None:
            return False

        kind, amount = pre_increment_id
        shot_counter_key = f"shots:active:{username}"
        job_counter_key = f"jobs:active:{username}"

        try:
            if kind == "shots":
                new_shot_count = self.redis_client.decrby(shot_counter_key, amount)
                new_job_count = self.redis_client.decr(job_counter_key)

                # Ensure non-negative (defensive)
                if new_shot_count < 0:
                    self.redis_client.set(shot_counter_key, 0)
                if new_job_count < 0:
                    self.redis_client.set(job_counter_key, 0)
            elif kind == "jobs":
                # Only decrement job counter (used for sweeps)
                new_job_count = self.redis_client.decr(job_counter_key)
                if new_job_count is not None and int(new_job_count) < 0:
                    self.redis_client.set(job_counter_key, 0)
            else:
                logger.warning("Unknown pre_increment_id kind: %s", kind)
                return False

            logger.info("Rolled back reservation for %s (kind=%s)", username, kind)
            return True

        except Exception as e:
            logger.exception("Redis error during rollback for %s: %s", username, e)
            return False


class ReservationResult:
    """Result of attempting to reserve submission capacity.

    Attributes:
        allowed: True if submission is allowed, False if over limit
        shots_after: Shot counter value after reservation (or rollback)
        jobs_after: Job counter value after reservation (or rollback)
        pre_increment_id: Tuple of (username, shot_increment) for later rollback
        limit_exceeded: True if reservation denied due to limit
        redis_error: Error message if Redis connection failed
    """

    def __init__(
        self,
        allowed: bool,
        shots_after: int | None = None,
        jobs_after: int | None = None,
        pre_increment_id: tuple | None = None,
        limit_exceeded: bool = False,
        redis_error: str | None = None,
    ):
        self.allowed = allowed
        self.shots_after = shots_after
        self.jobs_after = jobs_after
        self.pre_increment_id = pre_increment_id
        self.limit_exceeded = limit_exceeded
        self.redis_error = redis_error

    def __repr__(self) -> str:
        return f"ReservationResult(allowed={self.allowed}, shots={self.shots_after}, jobs={self.jobs_after})"
