"""IQM vendor plugin implementation.

Implements the VendorPlugin protocol by delegating to IQM-specific
submodules for request parsing, response parsing, header building,
job status fetching, and calibration handling.
"""

from __future__ import annotations

import logging
from typing import Any

from middleware.plugins.datatypes import (
    ArtifactClassification,
    JobStatusResult,
    JobSubmission,
    RoutesConfig,
    SubmissionResult,
)
from middleware.vendors.iqm import calibration as cal_module
from middleware.vendors.iqm.config import (
    DEFAULT_DEPRECATED_ROUTES,
    DEFAULT_LOGGED_ROUTES,
    DEFAULT_PUBLIC_ROUTES,
    DEFAULT_ROLE_ROUTES,
    IQMSettings,
)
from middleware.vendors.iqm.headers import build_machine_headers
from middleware.vendors.iqm.job_status import fetch_job_status
from middleware.vendors.iqm.request_parser import (
    classify_job_type,
    count_circuits_in_body,
    extract_project_from_metadata,
    extract_shots_from_body,
)
from middleware.vendors.iqm.response_parser import (
    extract_artifact_types_from_response_text,
    extract_jobid_from_response_text,
)

logger = logging.getLogger(__name__)

# Terminal statuses for IQM jobs
_TERMINAL_STATUSES = frozenset({"completed", "failed", "aborted", "deletion failed", "deleted"})


class IQMVendorPlugin:
    """IQM quantum computer vendor plugin."""

    def __init__(self, settings: Any) -> None:
        self._iqm_settings = IQMSettings()
        self._token = self._iqm_settings.IQM_SERVER_TOKEN
        # Allow core settings to override IQM defaults via env
        self._role_routes = getattr(settings, "ROLE_ROUTES", None) or DEFAULT_ROLE_ROUTES
        self._logged_routes = getattr(settings, "LOGGED_ROUTES", None) or DEFAULT_LOGGED_ROUTES
        self._minio_server_url = getattr(settings, "MINIO_SERVER_URL", "")
        self._bucket_name = getattr(settings, "BUCKET_NAME", "")

    def get_routes_config(self) -> RoutesConfig:
        return RoutesConfig(
            role_routes=self._role_routes,
            logged_routes=self._logged_routes,
            deprecated_routes=DEFAULT_DEPRECATED_ROUTES,
            public_routes=DEFAULT_PUBLIC_ROUTES,
        )

    def parse_submission_request(self, body: bytes, path: str) -> JobSubmission:
        return JobSubmission(
            shots=extract_shots_from_body(body),
            circuits=count_circuits_in_body(body),
            project=extract_project_from_metadata(body),
            job_type=classify_job_type(path),
        )

    def parse_submission_response(self, response_text: str) -> SubmissionResult:
        return SubmissionResult(
            job_id=extract_jobid_from_response_text(response_text),
            artifact_types=extract_artifact_types_from_response_text(response_text),
        )

    def build_upstream_headers(self, base_headers: dict[str, str]) -> dict[str, str]:
        headers = dict(base_headers)
        headers.update(build_machine_headers(self._token))
        return headers

    def get_terminal_statuses(self) -> set[str]:
        return set(_TERMINAL_STATUSES)

    def get_job_status(
        self,
        job_id: str,
        machine_url: str,
        headers: dict[str, str],
        timeout: float,
        verify_tls: bool,
    ) -> JobStatusResult:
        return fetch_job_status(job_id, machine_url, headers, timeout, verify_tls)

    def get_artifact_url(self, job_id: str, artifact_type: str) -> str:
        return f"/api/v1/jobs/{job_id}/artifacts/{artifact_type}"

    def get_payload_url(self, job_id: str) -> str:
        return f"/api/v1/jobs/{job_id}/payload"

    def classify_artifacts(self, status: str, available: list[str]) -> ArtifactClassification:
        if status == "failed":
            error_logs = [t for t in available if t == "error_log"]
            return ArtifactClassification(
                types_to_fetch=error_logs or ["error_log"],
                include_payload=True,
            )
        return ArtifactClassification(
            types_to_fetch=[t for t in available if t != "error_log"],
            include_payload=True,
        )

    def get_health_endpoint(self) -> str:
        return "/api/v1/quantum-computers/default/health"

    def get_calibration_poll_interval(self) -> int:
        return self._iqm_settings.CALIBRATION_POLL_INTERVAL

    def process_calibration_runs(
        self,
        machine_url: str,
        headers: dict[str, str],
        uploader: Any,
        db_init_fn: Any,
        timeout: float,
        verify_tls: bool,
    ) -> None:
        cal_module.process_calibration_runs(
            machine_url=machine_url,
            headers=headers,
            uploader=uploader,
            db_init_fn=db_init_fn,
            timeout=timeout,
            verify_tls=verify_tls,
        )

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
        return cal_module.enrich_artifact_locations_with_calibration(
            uploader=uploader,
            job_json=job_json,
            username=username,
            jobid=jobid,
            machine_url=machine_url,
            headers=headers,
            timeout=timeout,
            verify_tls=verify_tls,
            minio_server_url=self._minio_server_url,
            bucket_name=self._bucket_name,
        )
