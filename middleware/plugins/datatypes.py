"""Shared data classes exchanged between the core proxy and plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RoutesConfig:
    """Route definitions provided by a vendor plugin."""

    role_routes: dict[tuple[str, str], list[str]]
    logged_routes: dict[str, list[str]]
    deprecated_routes: list[str]
    public_routes: list[str]


@dataclass
class JobSubmission:
    """Parsed information from a job submission request body."""

    shots: int | None = None
    circuits: int = 0
    project: str | None = None
    job_type: str = ""  # e.g. "circuit", "sweep"


@dataclass
class SubmissionResult:
    """Parsed information from the upstream submission response."""

    job_id: str = ""
    artifact_types: list[str] = field(default_factory=list)


@dataclass
class JobStatusResult:
    """Job status fetched from the vendor's API."""

    status: str | None = None
    timeline: list[dict[str, Any]] = field(default_factory=list)
    available_artifacts: list[str] = field(default_factory=list)
    calibration_id: str | None = None
    raw_json: dict[str, Any] = field(default_factory=dict)

    @property
    def execution_start(self) -> str:
        """Extract execution start timestamp from the timeline."""
        for item in self.timeline:
            if item.get("status") == "execution_started":
                return item.get("timestamp", "")
        return ""

    @property
    def execution_end(self) -> str:
        """Extract execution end timestamp from the timeline."""
        for item in self.timeline:
            if item.get("status") == "execution_ended":
                return item.get("timestamp", "")
        return ""


@dataclass
class ArtifactClassification:
    """Which artifacts to fetch for a given terminal job status."""

    types_to_fetch: list[str] = field(default_factory=list)
    include_payload: bool = True


@dataclass
class JobAuthorizationResult:
    """Result of a site-level job authorization check."""

    is_authorized: bool = True
    status_code: int | None = None
    error_detail: Any = None


@dataclass
class JobReportResult:
    """Result of a job report operation."""

    success: bool = False
    method: str | None = None
    status_code: int | None = None
    response_text: str = ""
    error_detail: str = ""
