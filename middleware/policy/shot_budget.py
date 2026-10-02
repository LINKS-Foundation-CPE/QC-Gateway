"""Shot-budget policy plugin — license-derived concurrent-shot budgets.

The budget caps *concurrent
active shots* (the existing ``shots:active:{username}`` counter), not
cumulative shots. Licenses are the unit of accounting:

    budget = min(n_licenses x SHOTS_PER_LICENSE, MAX_CONCURRENT_SHOTS)

``n_licenses`` comes from the cluster: for a ``sqed`` principal it is the sum
over live token fields (``principal.metadata["n_licenses"]``), which SLURM
already reserved for the lifetime of the job. Nothing is claimed from a pool —
SLURM is the only allocator (decision 2026-08-04, no cloud channel), so any
other principal falls back to the flat ``MAX_CONCURRENT_SHOTS`` limit.

Sweep submissions are unaffected by the shot budget: they remain limited by
``MAX_CONCURRENT_SWEEPS`` exactly as under the passthrough plugin.
"""

from __future__ import annotations

import logging
from typing import Any

from middleware.concurrency import ConcurrencyLimiter
from middleware.plugins.datatypes import JobSubmission, PolicyDecision, Principal
from middleware.policy.shot_budget_config import ShotBudgetSettings

logger = logging.getLogger(__name__)


class ShotBudgetPolicyPlugin:
    """License-derived per-principal concurrent-shot budgets."""

    name = "shot_budget"

    def __init__(self, settings: Any, limiter: ConcurrencyLimiter) -> None:
        self._core = settings
        self._settings = ShotBudgetSettings()
        self._limiter = limiter

    def _n_licenses(self, principal: Principal) -> int:
        if principal.auth_source == "sqed":
            return max(int(principal.metadata.get("n_licenses", 0)), 0)
        # Not from the cluster, so no license count to derive a budget from: the
        # flat cap applies, which is what MAX_CONCURRENT_SHOTS // SHOTS_PER_LICENSE
        # expresses once _budget() takes the minimum.
        return max(int(self._core.MAX_CONCURRENT_SHOTS) // self._settings.SHOTS_PER_LICENSE, 1)

    def _budget(self, n_licenses: int) -> int:
        return min(
            n_licenses * self._settings.SHOTS_PER_LICENSE,
            int(self._core.MAX_CONCURRENT_SHOTS),
        )

    async def check_submission(
        self, principal: Principal, submission: JobSubmission
    ) -> PolicyDecision:
        n_licenses = self._n_licenses(principal)

        # Sweeps keep the flat sweep limit; no shot budget.
        is_sweep = submission.job_type == "sweep" or submission.shots is None
        if is_sweep:
            result = self._limiter.try_reserve(
                username=principal.username,
                shots=submission.shots,
                circuits=submission.circuits,
                job_type=submission.job_type,
            )
            if not result.allowed:
                return PolicyDecision(
                    allowed=False,
                    reason=(
                        f"Max concurrent sweeps reached "
                        f"({self._core.MAX_CONCURRENT_SWEEPS}) for user {principal.username}"
                    ),
                    status_code=429,
                )
            return PolicyDecision(
                allowed=True,
                reservation={
                    "username": principal.username,
                    "pre_increment_id": result.pre_increment_id,
                },
            )

        if n_licenses <= 0:
            return PolicyDecision(
                allowed=False,
                reason=f"No licenses held by user {principal.username}",
                status_code=429,
            )

        budget = self._budget(n_licenses)
        result = self._limiter.try_reserve(
            username=principal.username,
            shots=submission.shots,
            circuits=submission.circuits,
            job_type=submission.job_type,
            max_shots_override=budget,
        )
        if not result.allowed:
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"Concurrent shot budget reached ({budget} = {n_licenses} license(s)) "
                    f"for user {principal.username}"
                ),
                status_code=429,
            )

        logger.info(
            "User %s: budget %s (%s license(s), source=%s), active shots %s",
            principal.username,
            budget,
            n_licenses,
            principal.auth_source,
            result.shots_after,
        )
        return PolicyDecision(
            allowed=True,
            reservation={
                "username": principal.username,
                "pre_increment_id": result.pre_increment_id,
            },
        )

    async def on_submission_accepted(self, decision: PolicyDecision, job_id: str) -> None:
        """Nothing to persist: the cluster owns the licenses, not this plugin."""
        return None

    async def rollback(self, decision: PolicyDecision) -> None:
        if not decision.reservation:
            return
        self._limiter.rollback(
            decision.reservation["username"],
            decision.reservation["pre_increment_id"],
        )
