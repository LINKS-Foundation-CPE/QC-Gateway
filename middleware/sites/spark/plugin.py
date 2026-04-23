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
    ) -> JobAuthorizationResult:
        return await self._auth_checker.check(
            username=username,
            project_name=project_name,
            extra_headers=extra_headers,
            timeout=timeout,
        )

    async def report_job_async(self, job_id: str, payload: dict[str, Any]) -> JobReportResult:
        return await self._async_reporter.report_job(job_id, payload)

    def report_job_sync(self, job_id: str, payload: dict[str, Any]) -> JobReportResult:
        return self._sync_reporter.report_job(job_id, payload)

    async def send_initial_report(self, payload: dict[str, Any]) -> JobReportResult:
        return await self._async_reporter.send_initial_report(payload)

    def build_results_url(self, index_url: str, job_type: str) -> str:
        index_url = index_url.removesuffix("/index.html")
        if job_type == "sweep":
            return index_url + "/index.html"
        return f"https://jobs.{self._base_domain}/index.html?job={index_url}"
