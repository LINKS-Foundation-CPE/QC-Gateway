"""SPARK portal job authorization.

Checks with the SPARK portal's /jobAuthorizer endpoint whether a user
is allowed to submit a job.
"""

from __future__ import annotations

import logging

import httpx

from middleware.plugins.datatypes import JobAuthorizationResult

logger = logging.getLogger(__name__)


class SparkJobAuthorizationChecker:
    """Checks job authorization against the SPARK portal API."""

    def __init__(self, portal_api_host: str) -> None:
        self.portal_api_host = portal_api_host

    async def check(
        self,
        username: str,
        project_name: str | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> JobAuthorizationResult:
        """Call /jobAuthorizer on the portal to check if the job is allowed."""
        try:
            payload = {"username": username}
            if project_name is not None:
                payload["project_name"] = project_name

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.portal_api_host}/jobAuthorizer",
                    json=payload,
                    headers=extra_headers or {},
                    timeout=timeout,
                )

            if resp.status_code == 200:
                return JobAuthorizationResult(is_authorized=True)

            try:
                detail = resp.json()
            except Exception:
                detail = resp.text

            return JobAuthorizationResult(
                is_authorized=False,
                status_code=resp.status_code,
                error_detail=detail,
            )
        except Exception as e:
            logger.exception("Job authorization check failed for %s: %s", username, e)
            return JobAuthorizationResult(
                is_authorized=False,
                status_code=500,
                error_detail=f"Authorization service error: {e!s}",
            )
