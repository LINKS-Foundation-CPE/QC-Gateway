# Modularization Guide: Plugin Architecture, Authorization, Concurrency, and Reporting

This document explains the plugin architecture and how to use the modular
components extracted from the middleware. These components can be used
independently in different contexts and applications.

## Overview

The middleware is organized into three layers:

1. **Generic core** — framework-agnostic proxy logic (authentication, RBAC, concurrency, storage, metrics)
2. **Vendor plugins** — quantum computer vendor-specific API integration (currently: IQM)
3. **Site plugins** — deployment site-specific authorization and reporting (currently: SPARK)

## Plugin Interfaces

### VendorPlugin Protocol (`middleware/plugins/interfaces.py`)

Defines the contract for vendor-specific quantum computer integration:

```python
from middleware.plugins.interfaces import VendorPlugin
from middleware.plugins.datatypes import (
    RoutesConfig, JobSubmission, SubmissionResult,
    JobStatusResult, ArtifactClassification,
)
```

Key methods:
- `get_routes_config() -> RoutesConfig` — route definitions
- `parse_submission_request(body, path) -> JobSubmission` — extract job metadata
- `parse_submission_response(response_text) -> SubmissionResult` — extract job ID
- `build_upstream_headers(base_headers) -> dict` — inject vendor auth
- `get_terminal_statuses() -> set[str]` — terminal status strings
- `get_job_status(job_id, ...) -> JobStatusResult` — poll job status
- `classify_artifacts(status, available) -> ArtifactClassification` — artifact filtering
- `process_calibration_runs(...)` — vendor-specific calibration

### SitePlugin Protocol (`middleware/plugins/interfaces.py`)

Defines the contract for site-specific authorization and reporting:

```python
from middleware.plugins.interfaces import SitePlugin
from middleware.plugins.datatypes import JobAuthorizationResult, JobReportResult
```

Key methods:
- `authorize_job(username, project_name, ...) -> JobAuthorizationResult`
- `report_job_async(job_id, payload) -> JobReportResult`
- `report_job_sync(job_id, payload) -> JobReportResult`
- `send_initial_report(payload) -> JobReportResult`
- `build_results_url(index_url, job_type) -> str`

### Shared Data Classes (`middleware/plugins/datatypes.py`)

Rich data structures exchanged between the core and plugins:

```python
from middleware.plugins.datatypes import (
    RoutesConfig,           # Route definitions (role_routes, logged_routes, etc.)
    JobSubmission,          # Parsed submission request (shots, circuits, project, job_type)
    SubmissionResult,       # Parsed submission response (job_id, artifact_types)
    JobStatusResult,        # Job status from vendor API (status, timeline, artifacts, calibration_id)
    ArtifactClassification, # Which artifacts to fetch (types_to_fetch, include_payload)
    JobAuthorizationResult, # Authorization check result (is_authorized, status_code, error_detail)
    JobReportResult,        # Report operation result (success, method, status_code, response_text)
)
```

## Generic Modules

### 1. authorization.py — Role-Based Access Control

Generic RBAC that works with any vendor's route definitions.

```python
from middleware.authorization import RoleAuthorizationChecker
from middleware.authentication import User

# Routes come from vendor plugin: vendor_plugin.get_routes_config().role_routes
role_routes = {
    ("/api/v1/jobs", "POST"): ["admin", "user"],
    ("/api/v1/admin", "*"): ["admin"],
}
checker = RoleAuthorizationChecker(role_routes)

user = User(id="123", username="alice", roles=["user"])
if checker.check("/api/v1/jobs", "POST", user):
    print("Access granted")
```

### 2. concurrency.py — Per-User Concurrency Limiting

Redis-based concurrency limits, independent of any vendor or site.

```python
from middleware.concurrency import ConcurrencyLimiter
import redis

redis_client = redis.Redis(host="localhost", port=6379)
limiter = ConcurrencyLimiter(
    redis_client=redis_client,
    max_concurrent_shots=10000,
)

result = limiter.try_reserve("alice", shots=100, circuits=5)
if result.allowed:
    # proceed with submission
    if submission_failed:
        limiter.rollback("alice", result.pre_increment_id)
```

### 3. reporting.py — Generic Job Reporting

Base implementations for sending job reports. The SPARK site plugin uses these
internally, but they can also be used standalone.

```python
from middleware.reporting import JobReporter, SyncJobReporter

# Async (FastAPI middleware)
reporter = JobReporter("https://portal.example.com", timeout=10.0)
result = await reporter.report_job("job-123", payload)

# Sync (background worker)
sync_reporter = SyncJobReporter("https://portal.example.com", timeout=10.0)
result = sync_reporter.report_job("job-123", payload)
```

## IQM Vendor Plugin

The IQM plugin (`middleware/vendors/iqm/`) implements `VendorPlugin` for IQM quantum computers.

### Loading

```python
from middleware.plugins.loader import load_vendor_plugin
from middleware.config import Settings

settings = Settings()  # with VENDOR_PLUGIN=iqm
vendor = load_vendor_plugin(settings)
```

### Direct Usage

```python
from middleware.vendors.iqm.plugin import IQMVendorPlugin
from middleware.config import Settings

vendor = IQMVendorPlugin(Settings())

# Get route definitions
routes = vendor.get_routes_config()
print(routes.role_routes)
# {("/api/v1/jobs/default/circuit", "*"): ["cortex_user"], ...}

# Parse a submission
submission = vendor.parse_submission_request(request_body, "/api/v1/jobs/default/circuit")
print(submission.shots, submission.circuits, submission.job_type)

# Parse upstream response
result = vendor.parse_submission_response(response_text)
print(result.job_id, result.artifact_types)

# Build upstream headers
headers = vendor.build_upstream_headers({"Content-Type": "application/json"})

# Check terminal statuses
if job_status in vendor.get_terminal_statuses():
    classification = vendor.classify_artifacts(job_status, available_artifacts)
```

### Submodules

The IQM plugin delegates to focused submodules that can also be used directly:

```python
from middleware.vendors.iqm.request_parser import (
    extract_shots_from_body,
    count_circuits_in_body,
    extract_project_from_metadata,
    classify_job_type,
)
from middleware.vendors.iqm.response_parser import (
    extract_jobid_from_response_text,
    extract_artifact_types_from_response_text,
)
from middleware.vendors.iqm.headers import build_machine_headers
from middleware.vendors.iqm.job_status import fetch_job_status
```

## SPARK Site Plugin

The SPARK plugin (`middleware/sites/spark/`) implements `SitePlugin` for the SPARK portal.

### Loading

```python
from middleware.plugins.loader import load_site_plugin
from middleware.config import Settings

settings = Settings()  # with SITE_PLUGIN=spark
site = load_site_plugin(settings)
```

### Direct Usage

```python
from middleware.sites.spark.plugin import SparkSitePlugin
from middleware.config import Settings

site = SparkSitePlugin(Settings())

# Authorize a job
result = await site.authorize_job("alice", project_name="quantum-test", ...)
if result.is_authorized:
    print("Job authorized")

# Report a job (async)
result = await site.report_job_async("job-123", payload)

# Report a job (sync, for background worker)
result = site.report_job_sync("job-123", payload)

# Build results URL
url = site.build_results_url("https://minio/bucket/alice/job-123", "circuit")
```

## Creating a New Vendor Plugin

To add support for a new quantum computer vendor (e.g., IBM Quantum):

```python
# middleware/vendors/ibm/plugin.py
from middleware.plugins.datatypes import (
    RoutesConfig, JobSubmission, SubmissionResult,
    JobStatusResult, ArtifactClassification,
)

class IBMVendorPlugin:
    def __init__(self, settings):
        self._api_key = settings.IBM_API_KEY  # from IBMSettings

    def get_routes_config(self) -> RoutesConfig:
        return RoutesConfig(
            role_routes={
                ("/api/jobs", "POST"): ["ibm_user"],
                ("/api/jobs", "GET"): ["ibm_user"],
            },
            logged_routes={"/api/jobs": ["POST"]},
            deprecated_routes=[],
            public_routes=["/health", "/metrics"],
        )

    def parse_submission_request(self, body: bytes, path: str) -> JobSubmission:
        # IBM-specific payload parsing
        ...

    def parse_submission_response(self, response_text: str) -> SubmissionResult:
        # IBM-specific response parsing
        ...

    def build_upstream_headers(self, base_headers: dict) -> dict:
        headers = dict(base_headers)
        headers["Authorization"] = f"apikey {self._api_key}"
        return headers

    def get_terminal_statuses(self) -> set[str]:
        return {"COMPLETED", "FAILED", "CANCELLED"}

    # ... implement remaining methods
```

Then register in `middleware/plugins/loader.py`:
```python
_VENDOR_REGISTRY["ibm"] = "middleware.vendors.ibm.plugin.IBMVendorPlugin"
```

## Creating a New Site Plugin

To add support for a different authorization/reporting backend (e.g., an HPC centre with SLURM):

```python
# middleware/sites/hpc/plugin.py
from middleware.plugins.datatypes import JobAuthorizationResult, JobReportResult

class HPCSitePlugin:
    def __init__(self, settings):
        self._slurm_api = settings.SLURM_API_URL

    async def authorize_job(self, username, project_name, extra_headers, timeout):
        # Check SLURM allocation/budget
        ...

    async def report_job_async(self, job_id, payload):
        # Log to HPC accounting system
        ...

    def report_job_sync(self, job_id, payload):
        # Synchronous version for background worker
        ...

    async def send_initial_report(self, payload):
        # Record job submission in HPC system
        ...

    def build_results_url(self, index_url, job_type):
        # HPC-specific results URL
        return f"{self._hpc_portal}/results?job={index_url}"
```

Then register in `middleware/plugins/loader.py`:
```python
_SITE_REGISTRY["hpc"] = "middleware.sites.hpc.plugin.HPCSitePlugin"
```

## Integration Example: Custom Job Pipeline

```python
from middleware.plugins.loader import load_vendor_plugin, load_site_plugin
from middleware.concurrency import ConcurrencyLimiter
from middleware.config import Settings
import redis

settings = Settings()
vendor = load_vendor_plugin(settings)
site = load_site_plugin(settings)

redis_client = redis.Redis(host="localhost", port=6379)
limiter = ConcurrencyLimiter(redis_client, max_concurrent_shots=10000)

async def submit_job(username, request_body, path):
    # Parse request via vendor plugin
    submission = vendor.parse_submission_request(request_body, path)

    # Authorize via site plugin
    auth = await site.authorize_job(username, submission.project, {}, timeout=10)
    if not auth.is_authorized:
        return {"error": auth.error_detail}

    # Check concurrency
    reservation = limiter.try_reserve(username, submission.shots, submission.circuits)
    if not reservation.allowed:
        return {"error": "Rate limited"}

    # Proxy to upstream with vendor headers
    headers = vendor.build_upstream_headers({"Content-Type": "application/json"})
    # ... send request ...

    # Parse response
    result = vendor.parse_submission_response(response_text)

    # Report via site plugin
    await site.send_initial_report({"jobid": result.job_id, "username": username, ...})

    return {"job_id": result.job_id}
```

## Best Practices

1. **Plugin design**: Keep plugins focused — a vendor plugin should only know about its vendor's API, a site plugin should only know about its site's systems.
2. **Error handling**: Always check result dataclasses (`JobReportResult.success`, `JobAuthorizationResult.is_authorized`, `ReservationResult.allowed`).
3. **Timeouts**: Set appropriate timeouts in plugin constructors based on your deployment.
4. **Fail-open**: The core middleware is defensive — Redis/MinIO failures don't block proxying. Plugins should follow the same principle for non-critical operations.
5. **Testing**: Each plugin can be tested independently by mocking the upstream API it communicates with.
