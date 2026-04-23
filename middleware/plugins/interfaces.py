"""Protocol definitions for vendor and site plugins."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from middleware.plugins.datatypes import (
    ArtifactClassification,
    JobAuthorizationResult,
    JobReportResult,
    JobStatusResult,
    JobSubmission,
    RoutesConfig,
    SubmissionResult,
)


@runtime_checkable
class VendorPlugin(Protocol):
    """Contract for vendor-specific quantum computer integration.

    A vendor plugin encapsulates everything specific to how a particular
    quantum computer vendor's API works: route definitions, request/response
    parsing, authentication header injection, job status polling, artifact
    fetching, and calibration handling.
    """

    def get_routes_config(self) -> RoutesConfig:
        """Return route definitions for this vendor."""
        ...

    def parse_submission_request(self, body: bytes, path: str) -> JobSubmission:
        """Extract shots, circuits, project, job_type from a submission request."""
        ...

    def parse_submission_response(self, response_text: str) -> SubmissionResult:
        """Extract job_id and artifact_types from the upstream response."""
        ...

    def build_upstream_headers(self, base_headers: dict[str, str]) -> dict[str, str]:
        """Inject vendor-specific auth into headers for upstream proxying."""
        ...

    def get_terminal_statuses(self) -> set[str]:
        """Return status strings considered terminal for this vendor."""
        ...

    def get_job_status(
        self,
        job_id: str,
        machine_url: str,
        headers: dict[str, str],
        timeout: float,
        verify_tls: bool,
    ) -> JobStatusResult:
        """Query the vendor API for authoritative job status."""
        ...

    def get_artifact_url(self, job_id: str, artifact_type: str) -> str:
        """Return the relative URL path to fetch a specific artifact."""
        ...

    def get_payload_url(self, job_id: str) -> str:
        """Return the relative URL path to fetch the job payload."""
        ...

    def classify_artifacts(self, status: str, available: list[str]) -> ArtifactClassification:
        """Determine which artifacts to fetch based on terminal status."""
        ...

    def get_health_endpoint(self) -> str:
        """Return the vendor-specific health check path."""
        ...

    def get_calibration_poll_interval(self) -> int:
        """Return the cadence (seconds) at which the core worker should call
        ``process_calibration_runs``. Vendors without calibration may return a
        large value or ``0`` to effectively disable polling.
        """
        ...

    def process_calibration_runs(
        self,
        machine_url: str,
        headers: dict[str, str],
        uploader: Any,
        db_init_fn: Any,
        timeout: float,
        verify_tls: bool,
    ) -> None:
        """Run vendor-specific calibration polling (optional)."""
        ...

    def enrich_artifacts_with_calibration(
        self,
        uploader: Any,
        job_json: dict[str, Any],
        username: str,
        jobid: str,
        machine_url: str,
        headers: dict[str, str],
        timeout: float,
        verify_tls: bool,
    ) -> dict[str, str]:
        """Add calibration-related artifact URLs (optional)."""
        ...


@runtime_checkable
class SitePlugin(Protocol):
    """Contract for site-specific authorization and reporting.

    A site plugin encapsulates how a specific deployment site handles
    job authorization (e.g., portal API, SLURM accounting) and job
    reporting (e.g., portal jobReport endpoint, HPC billing).
    """

    async def authorize_job(
        self,
        username: str,
        project_name: str | None,
        extra_headers: dict[str, str] | None,
        timeout: float,
    ) -> JobAuthorizationResult:
        """Check whether a user is allowed to submit a job."""
        ...

    async def report_job_async(self, job_id: str, payload: dict[str, Any]) -> JobReportResult:
        """Send a job report asynchronously (middleware request path)."""
        ...

    def report_job_sync(self, job_id: str, payload: dict[str, Any]) -> JobReportResult:
        """Send a job report synchronously (background worker)."""
        ...

    async def send_initial_report(self, payload: dict[str, Any]) -> JobReportResult:
        """Send the initial job submission report."""
        ...

    def build_results_url(self, index_url: str, job_type: str) -> str:
        """Construct the user-facing results URL."""
        ...
