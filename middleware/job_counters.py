"""Redis / per-user counter helpers for the job-reporter.

Moved from `app.job_reporter` to keep counter logic testable and reusable.
Provides:
- get_redis_client() -> connected redis.Redis | None
- decrement_user_counters(r, username, circuits_count, shots_val, job_type)
"""

import redis

from middleware.concurrency import counter_keys
from middleware.config import settings

REDIS_HOST = settings.REDIS_HOST
REDIS_PORT = int(settings.REDIS_PORT)
REDIS_DB = int(settings.REDIS_DB)
REDIS_PASSWORD = settings.REDIS_PASSWORD or None


def get_redis_client():
    """Return a connected Redis client or None on failure.

    Performs a simple PING to verify connectivity; callers should handle
    ``None`` (degraded mode).
    """
    try:
        client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        client.ping()
        return client
    except Exception as e:
        logger.warning("Redis unavailable: %s", e)
        return None


def decrement_user_counters(r, username, circuits_count, shots_val, job_type=None):
    """Release a finished job's hold on the per-user Redis counters.

    Mirrors what admission took (``ConcurrencyLimiter.try_reserve``):
    - every job: `jobs:active:{username}` by 1
    - a sweep (``job_type == "sweep"``, or no shot count): `sweeps:active` by 1,
      and the shot counter is left alone, since a sweep never added to it
    - a circuit job: `shots:active:{username}` by `shots * circuits` (or 1)

    Counters are clamped at 0, and errors are logged rather than raised.
    """
    shot_key, job_key, sweep_key = counter_keys(username)
    is_sweep = job_type == "sweep" or not shots_val
    try:
        keys = [job_key] + ([sweep_key] if is_sweep else [])
        for key in keys:
            try:
                new_val = r.decr(key)
            except Exception:
                new_val = None
            if new_val is not None and int(new_val) < 0:
                r.set(key, 0)

        if not is_sweep:
            decrement_amount = (
                (int(shots_val) * int(circuits_count)) if shots_val and circuits_count else 1
            )
            logger.debug("Decrementing shots for %s by %d", username, decrement_amount)
            new_shots_val = r.decrby(shot_key, decrement_amount)
            if new_shots_val is not None and int(new_shots_val) < 0:
                r.set(shot_key, 0)
    except Exception as e:
        logger.exception("Redis counter decrement error for %s: %s", username, e)


# --- Prometheus metrics moved into this module ---
import asyncio
import logging

from prometheus_client import Gauge

logger = logging.getLogger(__name__)

# Prometheus gauge for total active jobs (sum of Redis counters)
total_jobs_gauge = Gauge(
    "station_control_total_active_jobs",
    "Total number of active jobs from Redis counters",
)


async def poll_redis_job_counters(redis_client):
    """Scan Redis for keys like 'jobs:active:*' and update Prometheus gauge.

    Runs blocking Redis calls in a thread via `asyncio.to_thread` so that the
    event loop is not blocked.
    """
    if redis_client is None:
        logger.debug("No Redis client available for polling job counters")
        return

    def _scan_and_sum():
        cursor = 0
        pattern = "jobs:active:*"
        total = 0
        try:
            while True:
                cursor, keys = redis_client.scan(cursor=cursor, match=pattern, count=1000)
                for k in keys:
                    try:
                        val = redis_client.get(k)
                        try:
                            cnt = int(val) if val is not None else 0
                        except Exception:
                            cnt = 0
                        total += cnt
                    except Exception:
                        logger.exception(f"Error processing Redis key {k}")
                        continue
                if cursor == 0:
                    break
        except Exception:
            logger.exception("Error scanning Redis for job counters")
        return total

    total = await asyncio.to_thread(_scan_and_sum)
    try:
        total_jobs_gauge.set(total)
    except Exception:
        logger.exception("Failed to set total jobs gauge")


async def queue_metrics_worker(app, interval_seconds: float = 5.0):
    """Background worker that periodically polls queue job counters.

    Args:
        app: FastAPI application instance (accesses `app.state.redis`).
        interval_seconds: polling interval in seconds.
    """
    try:
        while True:
            try:
                await poll_redis_job_counters(getattr(app.state, "redis", None))
            except Exception:
                logger.exception("Error while polling Redis job counters in background worker")
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("Queue metrics worker cancelled")
        raise
