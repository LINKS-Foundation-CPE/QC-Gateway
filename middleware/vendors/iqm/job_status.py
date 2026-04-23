"""IQM-specific job status fetching."""

from __future__ import annotations

import logging

import requests

from middleware.plugins.datatypes import JobStatusResult

logger = logging.getLogger(__name__)


def fetch_job_status(
    job_id: str,
    machine_url: str,
    headers: dict[str, str],
    timeout: float,
    verify_tls: bool,
) -> JobStatusResult:
    """Query the IQM API for authoritative job status.

    Fetches ``/api/v1/jobs/{job_id}`` and parses status, timeline,
    artifact types, and calibration_set_id.
    """
    url = f"{machine_url}/api/v1/jobs/{job_id}"
    resp = requests.get(url, headers=headers, timeout=timeout, verify=verify_tls)
    resp.raise_for_status()
    job_json = resp.json()

    available_artifacts = [a.get("type") for a in (job_json.get("artifacts") or [])]

    calibration_id = job_json.get("compilation", {}).get("calibration_set_id")

    return JobStatusResult(
        status=job_json.get("status"),
        timeline=job_json.get("timeline", []),
        available_artifacts=available_artifacts,
        calibration_id=calibration_id,
        raw_json=job_json,
    )
