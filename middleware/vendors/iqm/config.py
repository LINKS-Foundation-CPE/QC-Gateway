"""IQM-specific configuration: routes, token, and calibration settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class IQMSettings(BaseSettings):
    """Settings specific to the IQM vendor plugin.

    These are read from the same environment as the core settings, using
    the same Pydantic-settings mechanism.
    """

    IQM_SERVER_TOKEN: str | None = None

    # The calibration endpoints (`/cocos/api/v4/calibration/runs` and each run's
    # report) now refuse the device token with 403 — it authenticates, and is
    # then denied — and accept only an administrator token. The job path keeps
    # the device token. Unset, calibration falls back to `IQM_SERVER_TOKEN`,
    # which is how every deployment behaved before and still works on a machine
    # that has not tightened that endpoint.
    ADMIN_IQM_TOKEN: str | None = None

    CALIBRATION_POLL_INTERVAL: int = 60 * 60

    model_config = {"extra": "ignore"}


# Default IQM route definitions
#
# The sweep path (`/run`) asks only for `cortex_user` here. Pulse access is
# decided downstream, by the site plugin's authorization call, from the
# platform's own record of the grant rather than from a realm role in the
# token — which is what lets an administrator grant or revoke it, and what
# covers principals whose tokens carry no roles at all.
#
# Two consequences worth knowing before changing this back or forward:
#
#   * The site call only happens in `MIDDLEWARE_MODE=production`, and only for
#     the routes in DEFAULT_LOGGED_ROUTES. `maintenance` is not a concern —
#     it answers every request with 503 before routing — but in
#     `authentication` and `reporting` the request proceeds and nothing gates
#     the sweep path beyond `cortex_user`.
#   * A deployment whose portal predates the `job_type` field in the
#     authorization payload will not check pulse access at all. Deploy the
#     backend first.
DEFAULT_ROLE_ROUTES: dict[tuple[str, str], list[str]] = {
    ("/api/v1/jobs/default/circuit", "*"): ["cortex_user"],
    ("/api/v1/jobs/default/run", "*"): ["cortex_user"],
    ("/api/v1/jobs", "GET"): ["cortex_user"],
    ("/api/v1/calibration-sets/default", "GET"): ["cortex_user"],
    # The dynamic quantum architecture, which every qiskit-iqm from 15.x
    # onwards fetches before it can even construct a backend. Read-only
    # metadata, so it is gated like the calibration set above — without an
    # entry here a current client gets 403 "path not allowed" and never
    # reaches submission.
    ("/api/v1/calibration/default/gates", "GET"): ["cortex_user"],
    # The static architecture, which is what clients paired with this
    # generation of the machine API fetch instead. Read-only metadata, same
    # gate.
    ("/api/v1/quantum-architecture", "GET"): ["cortex_user"],
    ("/api/v1/jobs/{job_id}/cancel", "POST"): ["cortex_user"],
    ("/api/v1/jobs", "DELETE"): ["cortex_user"],
    ("/api/v1/quantum-computers", "GET"): ["cortex_user"],
}

DEFAULT_LOGGED_ROUTES: dict[str, list[str]] = {
    "/api/v1/jobs/default/circuit": ["POST"],
    "/api/v1/jobs/default/run": ["POST"],
}

DEFAULT_DEPRECATED_ROUTES: list[str] = [
    "/cocos",
    "/station",
    "/api/v1/quantum-computers/default/timeslots", #prevent any user group to access timeslots endpoint, as it is never used
]

DEFAULT_PUBLIC_ROUTES: list[str] = [
    "/api/v1/quantum-computers/default/health",
    "/health",
    "/proxy-config",
    "/metrics",
]
