"""HPC site plugin (`SITE_PLUGIN=sqed`): submissions authorized by SLURM.

For a deployment where circuits are submitted from batch jobs on an HPC
cluster, with per-job tokens provisioned by the SLURM prolog (the `sqed` auth
plugin). Behaviour is deliberately Lagrange-like — reporting and results URLs
are identical to the SPARK site plugin, to which this plugin delegates. The one
difference is job authorization:

- ``sqed`` principals **skip site authorization**. SLURM admission (license
  grant, partition/account controls) already authorized the HPC job that
  minted the token; the portal's ``/jobAuthorizer`` verifies a Keycloak JWT
  that a ``sqed_`` bearer can never present. Budget/concurrency policy and
  job reporting for billing still apply.
- Any other principal (cloud/Keycloak) is authorized against the portal
  exactly like SPARK.
"""

from __future__ import annotations

import logging
from typing import Any

from middleware.plugins.datatypes import (
    JobAuthorizationResult,
    JobReportResult,
    Principal,
)
from middleware.sites.spark.plugin import SparkSitePlugin

logger = logging.getLogger(__name__)


class SqedSitePlugin:
    """SPARK behaviour, plus the SLURM-authorized path for `sqed` principals."""

    def __init__(self, settings: Any) -> None:
        self._spark = SparkSitePlugin(settings)

    async def authorize_job(
        self,
        principal: Principal,
        project_name: str | None,
        extra_headers: dict[str, str] | None,
        timeout: float,
        job_type: str | None = None,
    ) -> JobAuthorizationResult:
        if principal.auth_source == "sqed":
            logger.info(
                "sqed principal %s authorized by SLURM admission (jobs %s)",
                principal.username,
                principal.metadata.get("job_ids"),
            )
            return JobAuthorizationResult(is_authorized=True)
        # Forwarded, not dropped: a principal that reaches the portal check
        # must still be gated on what it is submitting — this is what makes
        # the Pulse (sweep) refusal apply on the sqed site too.
        return await self._spark.authorize_job(
            principal=principal,
            project_name=project_name,
            extra_headers=extra_headers,
            timeout=timeout,
            job_type=job_type,
        )

    async def report_job_async(self, job_id: str, payload: dict[str, Any]) -> JobReportResult:
        return await self._spark.report_job_async(job_id, payload)

    def report_job_sync(self, job_id: str, payload: dict[str, Any]) -> JobReportResult:
        return self._spark.report_job_sync(job_id, payload)

    async def send_initial_report(self, payload: dict[str, Any]) -> JobReportResult:
        return await self._spark.send_initial_report(payload)

    def build_results_url(self, index_url: str, job_type: str) -> str:
        return self._spark.build_results_url(index_url, job_type)
