"""Protocol definitions for vendor and site plugins."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from middleware.plugins.datatypes import (
    ArtifactClassification,
    JobAuthorizationResult,
    JobReportResult,
    JobStatusResult,
    JobSubmission,
    PolicyDecision,
    Principal,
    RoutesConfig,
    SubmissionResult,
)


class AuthError(Exception):
    """Definitive authentication failure — stop the chain and return 401.

    Distinct from a plugin returning ``None``, which means "not my token, try
    the next one". Raising means the plugin claimed the token and it failed,
    so falling through to another plugin would be wrong: it would turn a
    specific, reportable failure into a generic one.
    """

    def __init__(self, detail: str, status_code: int = 401) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@runtime_checkable
class AuthPlugin(Protocol):
    """Contract for a token-validating authentication backend.

    Routing, implemented in :func:`middleware.authentication.authenticate`:

    1. A token matching a plugin's declared prefix goes to that plugin alone.
       A prefix is a hard claim — there is no fallback, because a token that
       announces its issuer and then fails is a failure, not a near miss.
    2. A token with no recognised prefix is offered to the prefix-less plugins
       in configuration order, and the first ``Principal`` wins.

    That ordering is what lets a deployment add a second token format without
    touching the first: OIDC JWTs carry no prefix and stay in the fallback
    chain, so an added prefix-owning plugin cannot shadow them.
    """

    @property
    def name(self) -> str:
        """Stable identifier, used in configuration and log lines."""
        ...

    @property
    def prefixes(self) -> list[str]:
        """Token prefixes owned by this plugin (e.g. ``["sqed_"]``).

        An empty list means the plugin claims no prefix and participates in
        the fallback chain.
        """
        ...

    async def validate(self, token: str) -> Principal | None:
        """Validate ``token``.

        Return a ``Principal`` on success, ``None`` for "not my token", and
        raise ``AuthError`` when the token is this plugin's and is bad.
        """
        ...


@runtime_checkable
class PolicyPlugin(Protocol):
    """Contract for submission-time policy: quota, budget, concurrency.

    Answers "may this principal submit *this*, right now" — which is a
    deployment question, not a property of the machine. One site limits
    concurrent shots per user with a flat number; another derives the limit
    from licenses a batch scheduler has already reserved.

    Three calls rather than one, because a reservation has to be undoable:
    capacity is taken before the request is proxied, and the machine may still
    refuse it.
    """

    @property
    def name(self) -> str:
        """Stable identifier, used in configuration and log lines."""
        ...

    async def check_submission(
        self, principal: Principal, submission: JobSubmission
    ) -> PolicyDecision:
        """Called before proxying. A denied decision blocks the request."""
        ...

    async def on_submission_accepted(self, decision: PolicyDecision, job_id: str) -> None:
        """Called once the machine has accepted the submission (2xx).

        The commit point: whatever ``check_submission`` reserved is now
        attributable to a real job. ``decision`` is the object that call
        returned, so a plugin reads its own handles out of
        ``decision.reservation`` instead of keeping them on the instance.
        """
        ...

    async def rollback(self, decision: PolicyDecision) -> None:
        """Undo the reservation: the machine refused, or the call failed."""
        ...


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

    def parse_artifact(self, artifact_type: str, data: bytes) -> Any | None:
        """Optionally decode a binary artifact into a JSON-serialisable view.

        The core stores every artifact verbatim and does not know what any of
        them mean. A vendor whose artifacts are binary can return a decoded,
        display-sized view here, which the core stores alongside the raw bytes
        so a viewer needs no vendor parser of its own.

        Return ``None`` for anything this plugin does not decode. Must not
        raise: the raw artifact is already stored by the time this is called,
        and a parse failure must not cost the job its results.
        """
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

    def build_calibration_headers(self) -> dict[str, str]:
        """Headers for the calibration endpoints.

        Separate from ``build_upstream_headers`` because a machine may demand more
        privilege for calibration data than for job traffic — IQM now does — and
        that credential must not ride along on user requests.
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
        job_type: str | None = None,
    ) -> JobAuthorizationResult:
        """Check whether a user is allowed to submit a job.

        ``job_type`` is the vendor plugin's classification of the request —
        for IQM, ``circuit`` or ``sweep``. It is passed so a site can make the
        decision depend on *what* is being submitted and not only on who is
        submitting and to which project; the sweep path is privileged.

        Keyword-only in effect and defaulted, so a site plugin written before
        this existed still satisfies the contract.
        """
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
