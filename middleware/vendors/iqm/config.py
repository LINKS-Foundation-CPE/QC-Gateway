"""IQM-specific configuration: routes, token, and calibration settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class IQMSettings(BaseSettings):
    """Settings specific to the IQM vendor plugin.

    These are read from the same environment as the core settings, using
    the same Pydantic-settings mechanism.
    """

    IQM_SERVER_TOKEN: str | None = None

    CALIBRATION_POLL_INTERVAL: int = 60 * 60

    model_config = {"extra": "ignore"}


# Default IQM route definitions
DEFAULT_ROLE_ROUTES: dict[tuple[str, str], list[str]] = {
    ("/api/v1/jobs/default/circuit", "*"): ["cortex_user"],
    ("/api/v1/jobs/default/sweep", "*"): ["pulla_user"],
    ("/api/v1/jobs", "GET"): ["cortex_user"],
    ("/api/v1/calibration-sets/default", "GET"): ["cortex_user"],
    ("/api/v1/jobs/{job_id}/cancel", "POST"): ["cortex_user"],
    ("/api/v1/jobs", "DELETE"): ["cortex_user"],
    ("/api/v1/quantum-computers", "GET"): ["cortex_user"],
}

DEFAULT_LOGGED_ROUTES: dict[str, list[str]] = {
    "/api/v1/jobs/default/circuit": ["POST"],
    "/api/v1/jobs/default/sweep": ["POST"],
}

DEFAULT_DEPRECATED_ROUTES: list[str] = [
    "/cocos",
    "/station",
]

DEFAULT_PUBLIC_ROUTES: list[str] = [
    "/api/v1/quantum-computers/default/health",
    "/proxy-config",
    "/metrics",
]
