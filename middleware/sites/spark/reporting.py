"""SPARK portal job reporting.

Wraps the generic JobReporter/SyncJobReporter with SPARK portal
specifics (PUT-first POST-fallback to /jobReport).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from middleware.plugins.datatypes import JobReportResult

logger = logging.getLogger(__name__)


class SparkAsyncReporter:
    """Async job reporter for the SPARK portal API."""

    def __init__(self, portal_api_host: str, timeout: float = 10.0) -> None:
        self.portal_api_host = portal_api_host
        self.timeout = timeout

    async def report_job(self, job_id: str, payload: dict[str, Any]) -> JobReportResult:
        """Send job report: PUT /jobReport/{id} then POST /jobReport fallback."""
        try:
            async with httpx.AsyncClient() as client:
                put_url = f"{self.portal_api_host}/jobReport/{job_id}"
                put_resp = await client.put(put_url, json=payload, timeout=self.timeout)

                if put_resp.status_code == 200:
                    return JobReportResult(
                        success=True,
                        method="PUT",
                        status_code=put_resp.status_code,
                        response_text=put_resp.text,
                    )

                post_url = f"{self.portal_api_host}/jobReport"
                post_resp = await client.post(post_url, json=payload, timeout=self.timeout)

                if post_resp.status_code == 200:
                    return JobReportResult(
                        success=True,
                        method="POST",
                        status_code=post_resp.status_code,
                        response_text=post_resp.text,
                    )

                return JobReportResult(
                    success=False,
                    method="POST",
                    status_code=post_resp.status_code,
                    response_text=post_resp.text,
                    error_detail=f"POST to {post_url} returned {post_resp.status_code}",
                )
        except Exception as e:
            logger.exception("Failed to send job report for %s: %s", job_id, e)
            return JobReportResult(success=False, error_detail=f"Exception: {e!s}")

    async def send_initial_report(self, payload: dict[str, Any]) -> JobReportResult:
        """POST initial job submission report to /jobReport."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.portal_api_host}/jobReport",
                    json=payload,
                    timeout=self.timeout,
                )
                return JobReportResult(
                    success=resp.status_code in range(200, 300),
                    method="POST",
                    status_code=resp.status_code,
                    response_text=resp.text,
                )
        except Exception as e:
            logger.exception("Failed to send initial jobReport: %s", e)
            return JobReportResult(success=False, error_detail=f"Exception: {e!s}")


class SparkSyncReporter:
    """Synchronous job reporter for the SPARK portal API (background worker)."""

    def __init__(self, portal_api_host: str, timeout: float = 10.0) -> None:
        self.portal_api_host = portal_api_host
        self.timeout = timeout

    def report_job(self, job_id: str, payload: dict[str, Any]) -> JobReportResult:
        """Send job report: PUT then POST fallback (synchronous)."""
        import requests as req

        try:
            put_url = f"{self.portal_api_host}/jobReport/{job_id}"
            put_resp = req.put(put_url, json=payload, timeout=self.timeout)

            if put_resp.status_code == 200:
                return JobReportResult(
                    success=True,
                    method="PUT",
                    status_code=put_resp.status_code,
                    response_text=put_resp.text,
                )

            post_url = f"{self.portal_api_host}/jobReport"
            post_resp = req.post(post_url, json=payload, timeout=self.timeout)

            if post_resp.status_code == 200:
                return JobReportResult(
                    success=True,
                    method="POST",
                    status_code=post_resp.status_code,
                    response_text=post_resp.text,
                )

            return JobReportResult(
                success=False,
                method="POST",
                status_code=post_resp.status_code,
                response_text=post_resp.text,
                error_detail=f"POST to {post_url} returned {post_resp.status_code}",
            )
        except Exception as e:
            logger.exception("Failed to send job report for %s: %s", job_id, e)
            return JobReportResult(success=False, error_detail=f"Exception: {e!s}")
