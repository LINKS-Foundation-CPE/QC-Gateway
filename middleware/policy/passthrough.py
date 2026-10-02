"""Passthrough policy plugin — the historical concurrency behavior.

Delegates straight to :class:`middleware.concurrency.ConcurrencyLimiter`
with the core limits, producing exactly the responses the middleware
produced before policy became pluggable. Richer budget models (e.g. the
license-derived shot budget) implement the same ``PolicyPlugin`` surface.
"""

from __future__ import annotations

import logging
from typing import Any

from middleware.concurrency import ConcurrencyLimiter
from middleware.plugins.datatypes import JobSubmission, PolicyDecision, Principal

logger = logging.getLogger(__name__)


class PassthroughPolicyPlugin:
    """Per-user concurrent-shots/sweeps limits via the shared limiter."""

    name = "passthrough"

    def __init__(self, settings: Any, limiter: ConcurrencyLimiter) -> None:
        self._settings = settings
        self._limiter = limiter

    async def check_submission(
        self, principal: Principal, submission: JobSubmission
    ) -> PolicyDecision:
        result = self._limiter.try_reserve(
            username=principal.username,
            shots=submission.shots,
            circuits=submission.circuits,
            job_type=submission.job_type,
        )
        if not result.allowed:
            if submission.job_type == "sweep":
                reason = (
                    f"Max concurrent sweeps reached "
                    f"({self._settings.MAX_CONCURRENT_SWEEPS}) for user {principal.username}"
                )
            else:
                reason = (
                    f"Max concurrent shots reached "
                    f"({self._settings.MAX_CONCURRENT_SHOTS}) for user {principal.username}"
                )
            return PolicyDecision(allowed=False, reason=reason, status_code=429)

        logger.info("Active shots for user %s: %s", principal.username, result.shots_after)
        return PolicyDecision(
            allowed=True,
            reservation={
                "username": principal.username,
                "pre_increment_id": result.pre_increment_id,
            },
        )

    async def on_submission_accepted(self, decision: PolicyDecision, job_id: str) -> None:
        return None

    async def rollback(self, decision: PolicyDecision) -> None:
        if not decision.reservation:
            return
        self._limiter.rollback(
            decision.reservation["username"],
            decision.reservation["pre_increment_id"],
        )
