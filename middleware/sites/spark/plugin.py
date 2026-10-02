"""SPARK site plugin implementation.

Implements the SitePlugin protocol by delegating to SPARK-specific
authorization and reporting modules.
"""

from __future__ import annotations

import logging
from typing import Any

from middleware.plugins.datatypes import JobAuthorizationResult, JobReportResult
from middleware.sites.spark.authorization import SparkJobAuthorizationChecker
from middleware.sites.spark.config import SparkSettings
from middleware.sites.spark.reporting import SparkAsyncReporter, SparkSyncReporter

logger = logging.getLogger(__name__)


class SparkSitePlugin:
    """SPARK portal site plugin."""

    def __init__(self, settings: Any) -> None:
        self._spark_settings = SparkSettings()
        portal_host = self._spark_settings.PORTAL_API_HOST
        base_domain = self._spark_settings.BASE_DOMAIN
        timeout = float(getattr(settings, "UPSTREAM_TIMEOUT", 30))

        self._base_domain = base_domain
        self._auth_checker = SparkJobAuthorizationChecker(portal_host)
        self._async_reporter = SparkAsyncReporter(portal_host, timeout=timeout)
        self._sync_reporter = SparkSyncReporter(portal_host, timeout=timeout)

    async def authorize_job(
        self,
        username: str,
        project_name: str | None,
        extra_headers: dict[str, str] | None,
        timeout: float,
        job_type: str | None = None,
    ) -> JobAuthorizationResult:
        return await self._auth_checker.check(
            username=username,
            project_name=project_name,
            extra_headers=extra_headers,
            timeout=timeout,
            job_type=job_type,
        )

    async def report_job_async(self, job_id: str, payload: dict[str, Any]) -> JobReportResult:
        return await self._async_reporter.report_job(job_id, payload)

    def report_job_sync(self, job_id: str, payload: dict[str, Any]) -> JobReportResult:
        return self._sync_reporter.report_job(job_id, payload)

    async def send_initial_report(self, payload: dict[str, Any]) -> JobReportResult:
        return await self._async_reporter.send_initial_report(payload)

    def build_results_url(self, index_url: str, job_type: str) -> str:
        """The URL reported to the portal as a job's results.

        Every job type gets the viewer, not a directory listing. A sweep used
        to be sent to the raw artifact index instead, because the viewer could
        not read protobuf — it now reads the parsed sidecars the reporter
        writes, so the exception has no reason left. The raw artifacts stay one
        click away: the viewer links the index in its own header.
        """
        index_url = index_url.removesuffix("/index.html")
        portal_url = self._spark_settings.JOBS_PORTAL_URL or f"https://jobs.{self._base_domain}"
        return f"{portal_url}/index.html?job={index_url}"
