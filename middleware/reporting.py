"""Job reporting and status update abstraction.

This module provides a high-level interface for job reporting that abstracts
away the details of communicating with the portal. It handles:
- Sending job reports (both initial submissions and terminal states)
- Updating job status
- Fallback logic (PUT then POST)

These classes are decoupled from HTTP middleware and database concerns,
making them reusable in different contexts.

Public classes:
- `JobReporter`: sends job reports and status updates to a portal API (async)
- `SyncJobReporter`: synchronous wrapper for job reporting
"""

import logging
from typing import Any

import httpx
import requests

logger = logging.getLogger(__name__)


class JobReporter:
    """Sends job reports and status updates to the portal API.

    This class encapsulates the job reporting logic that was previously embedded
    in both main.py and job_reporter.py. It handles:
    - Sending complete job reports (after terminal state)
    - Sending status-only updates
    - Fallback logic (tries PUT then POST)

    Usage:
        reporter = JobReporter("https://portal.example.com")
        result = await reporter.report_job(jobid, payload)
        if result.success:
            # Job reported successfully
    """

    def __init__(self, portal_api_host: str, timeout: float = 10.0):
        """Initialize the job reporter.

        Args:
            portal_api_host: Base URL for portal API (e.g., 'https://portal.example.com')
            timeout: Request timeout in seconds for all API calls
        """
        self.portal_api_host = portal_api_host
        self.timeout = timeout

    async def report_job(self, jobid: str, payload: dict[str, Any]) -> "JobReportResult":
        """Send a complete job report to the portal.

        Attempts PUT to /jobReport/{jobid} first (idempotent), then falls back
        to POST to /jobReport if PUT fails. This matches behavior of the
        original middleware.

        Args:
            jobid: The job ID
            payload: Report payload dict, must include 'username' and 'status'

        Returns:
            JobReportResult with success status and details
        """
        try:
            async with httpx.AsyncClient() as client:
                # Try PUT first (idempotent)
                put_url = f"{self.portal_api_host}/jobReport/{jobid}"
                logger.debug(f"Attempting PUT to {put_url}")

                put_resp = await client.put(put_url, json=payload, timeout=self.timeout)
                logger.info(f"PUT response: {put_resp.status_code}")

                if put_resp.status_code == 200:
                    logger.info(f"Job report sent via PUT for {jobid}")
                    return JobReportResult(
                        success=True,
                        method="PUT",
                        status_code=put_resp.status_code,
                        response_text=put_resp.text,
                    )

                # Fall back to POST
                logger.info(f"PUT failed with {put_resp.status_code}, trying POST")
                post_url = f"{self.portal_api_host}/jobReport"
                post_resp = await client.post(post_url, json=payload, timeout=self.timeout)
                logger.info(f"POST response: {post_resp.status_code}")

                if post_resp.status_code == 200:
                    logger.info(f"Job report sent via POST for {jobid}")
                    return JobReportResult(
                        success=True,
                        method="POST",
                        status_code=post_resp.status_code,
                        response_text=post_resp.text,
                    )

                # Both failed
                return JobReportResult(
                    success=False,
                    method="POST",
                    status_code=post_resp.status_code,
                    response_text=post_resp.text,
                    error_detail=f"POST to {post_url} returned {post_resp.status_code}",
                )

        except Exception as e:
            logger.exception(f"Failed to send job report for {jobid}: {e}")
            return JobReportResult(
                success=False, status_code=None, error_detail=f"Exception: {e!s}"
            )

    async def update_status(
        self, jobid: str, username: str, status: str, project_name: str | None = None
    ) -> "JobReportResult":
        """Send a status-only update for a job.

        This is used for non-terminal status changes (e.g., queued -> running).
        Attempts PUT then POST fallback like report_job.

        Args:
            jobid: The job ID
            username: Submitting user's username
            status: New status string
            project_name: Optional project name

        Returns:
            JobReportResult with success status and details
        """
        payload = {
            "jobid": jobid,
            "username": username,
            "status": status,
        }
        if project_name is not None:
            payload["project_name"] = project_name

        return await self.report_job(jobid, payload)


class JobReportResult:
    """Result of a job report operation.

    Attributes:
        success: True if report was accepted, False otherwise
        method: HTTP method used ("PUT" or "POST")
        status_code: HTTP status code returned, or None on client error
        response_text: Response text from the server
        error_detail: Error details string (only set if not successful)
    """

    def __init__(
        self,
        success: bool,
        method: str | None = None,
        status_code: int | None = None,
        response_text: str = "",
        error_detail: str | None = None,
    ):
        self.success = success
        self.method = method
        self.status_code = status_code
        self.response_text = response_text
        self.error_detail = error_detail or ""

    def __repr__(self) -> str:
        return (
            f"JobReportResult(success={self.success}, method={self.method}, "
            f"status_code={self.status_code})"
        )


class SyncJobReporter:
    """Synchronous wrapper for job reporting using requests library.

    This class provides the same interface as JobReporter but uses the
    synchronous requests library instead of httpx. It's designed for use
    in synchronous scripts and background workers.

    Usage:
        reporter = SyncJobReporter("https://portal.example.com")
        result = reporter.report_job(jobid, payload)
        if result.success:
            # Job reported successfully
    """

    def __init__(self, portal_api_host: str, timeout: float = 10.0):
        """Initialize the synchronous job reporter.

        Args:
            portal_api_host: Base URL for portal API (e.g., 'https://portal.example.com')
            timeout: Request timeout in seconds for all API calls
        """
        self.portal_api_host = portal_api_host
        self.timeout = timeout

    def report_job(self, jobid: str, payload: dict[str, Any]) -> JobReportResult:
        """Send a complete job report to the portal.

        Attempts PUT to /jobReport/{jobid} first (idempotent), then falls back
        to POST to /jobReport if PUT fails. This matches behavior of the
        original middleware.

        Args:
            jobid: The job ID
            payload: Report payload dict, must include 'username' and 'status'

        Returns:
            JobReportResult with success status and details
        """
        try:
            # Try PUT first (idempotent)
            put_url = f"{self.portal_api_host}/jobReport/{jobid}"
            logger.debug(f"Attempting PUT to {put_url}")

            put_resp = requests.put(put_url, json=payload, timeout=self.timeout)
            logger.info(f"PUT response: {put_resp.status_code}")

            if put_resp.status_code == 200:
                logger.info(f"Job report sent via PUT for {jobid}")
                return JobReportResult(
                    success=True,
                    method="PUT",
                    status_code=put_resp.status_code,
                    response_text=put_resp.text,
                )

            # Fall back to POST
            logger.info(f"PUT failed with {put_resp.status_code}, trying POST")
            post_url = f"{self.portal_api_host}/jobReport"
            post_resp = requests.post(post_url, json=payload, timeout=self.timeout)
            logger.info(f"POST response: {post_resp.status_code}")

            if post_resp.status_code == 200:
                logger.info(f"Job report sent via POST for {jobid}")
                return JobReportResult(
                    success=True,
                    method="POST",
                    status_code=post_resp.status_code,
                    response_text=post_resp.text,
                )

            # Both failed
            return JobReportResult(
                success=False,
                method="POST",
                status_code=post_resp.status_code,
                response_text=post_resp.text,
                error_detail=f"POST to {post_url} returned {post_resp.status_code}",
            )

        except Exception as e:
            logger.exception(f"Failed to send job report for {jobid}: {e}")
            return JobReportResult(
                success=False, status_code=None, error_detail=f"Exception: {e!s}"
            )

    def update_status(
        self, jobid: str, username: str, status: str, project_name: str | None = None
    ) -> JobReportResult:
        """Send a status-only update for a job.

        This is used for non-terminal status changes (e.g., queued -> running).
        Attempts PUT then POST fallback like report_job.

        Args:
            jobid: The job ID
            username: Submitting user's username
            status: New status string
            project_name: Optional project name

        Returns:
            JobReportResult with success status and details
        """
        payload = {
            "jobid": jobid,
            "username": username,
            "status": status,
        }
        if project_name is not None:
            payload["project_name"] = project_name

        return self.report_job(jobid, payload)
