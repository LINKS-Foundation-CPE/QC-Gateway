"""app.main — Generic HTTP proxy / middleware for quantum computer servers.

This module implements the HTTP middleware and helper endpoints that proxy
client requests to an upstream quantum computer server while performing
application-level concerns such as authentication, authorization, job
logging, object storage uploads and metrics collection.

Vendor-specific logic (e.g., IQM API parsing) and site-specific logic
(e.g., SPARK portal authorization/reporting) are loaded via plugins.
See ``middleware.plugins`` for the plugin interfaces.

Key responsibilities:
- Authenticate requests via JWT/Keycloak.
- Authorize endpoints via role-based access control (routes from vendor plugin).
- (Optional) Authorize job submissions via site plugin.
- Enforce per-user concurrency limits using Redis counters.
- Proxy requests to the upstream server (headers built by vendor plugin).
- Capture submitted job payloads, log them, and report via site plugin.
- Expose Prometheus metrics and lightweight health-check endpoints.

Design notes:
- The middleware is defensive: non-critical failures (Redis, MinIO, portal)
  are logged but do not block proxying by default.
- Shared resources are attached to ``app.state`` inside the ``lifespan``
  context manager.

Environment / configuration: see ``middleware.config.Settings``.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from middleware.authentication import get_current_user
from middleware.authorization import RoleAuthorizationChecker
from middleware.concurrency import ConcurrencyLimiter
from middleware.config import Settings
from middleware.db import log_job
from middleware.minio import S3Uploader
from middleware.plugins.interfaces import SitePlugin, VendorPlugin
from middleware.plugins.loader import load_site_plugin, load_vendor_plugin
from middleware.utils import build_json_response, build_raw_response, filter_response_headers

logging.basicConfig(
    level=getattr(logging, getattr(Settings(), "LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)
settings = Settings()
uploader = S3Uploader()
logger.warning("middleware starting (mode=%s, jwks=%s)", settings.MIDDLEWARE_MODE, settings.KEYCLOAK_JWKS_URL)

from middleware.artifacts import upload_artifact_from_response
from middleware.job_capture import upload_submitted_circuit
from middleware.job_counters import queue_metrics_worker

# Module-level references populated in lifespan
role_checker: RoleAuthorizationChecker | None = None
concurrency_limiter: ConcurrencyLimiter | None = None
vendor_plugin: VendorPlugin | None = None
site_plugin: SitePlugin | None = None
routes_config = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager.

    Loads vendor/site plugins, initializes shared resources, and attaches
    them to ``app.state``.
    """
    global role_checker, concurrency_limiter, vendor_plugin, site_plugin, routes_config

    # Load plugins
    vendor_plugin = load_vendor_plugin(settings)
    site_plugin = load_site_plugin(settings)
    routes_config = vendor_plugin.get_routes_config()

    # Initialize role checker from vendor-provided routes
    role_checker = RoleAuthorizationChecker(routes_config.role_routes)

    logger.info("VERIFY_UPSTREAM_SSL: %s", settings.VERIFY_UPSTREAM_SSL)
    logger.info("MACHINE_URL: %s", settings.MACHINE_URL)

    # Setup Redis
    try:
        from middleware.job_counters import get_redis_client

        redis_client = get_redis_client()
        if redis_client is not None:
            app.state.redis = redis_client
            logger.info("Connected to Redis for job counters")
        else:
            app.state.redis = None
            logger.warning("Redis client not available at startup")
    except Exception as e:
        logger.exception("Failed to initialize Redis client: %s", e)
        app.state.redis = None
        redis_client = None

    # Initialize concurrency limiter
    concurrency_limiter = ConcurrencyLimiter(
        redis_client=app.state.redis,
        max_concurrent_shots=int(settings.MAX_CONCURRENT_SHOTS),
        max_concurrent_sweeps=int(getattr(settings, "MAX_CONCURRENT_SWEEPS", 10)),
    )

    # Store on app.state
    app.state.vendor_plugin = vendor_plugin
    app.state.site_plugin = site_plugin
    app.state.routes_config = routes_config
    app.state.concurrency_limiter = concurrency_limiter

    async with httpx.AsyncClient(verify=settings.VERIFY_UPSTREAM_SSL) as client:
        app.state.http_client = client
        app.state.metrics_task = asyncio.create_task(queue_metrics_worker(app))
        try:
            yield
        finally:
            task = getattr(app.state, "metrics_task", None)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"https://{settings.FRONTEND_URL}/*"] if settings.FRONTEND_URL else ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


@app.middleware("http")
async def proxy_and_capture(request: Request, call_next):
    """Primary middleware that proxies requests and captures job metadata.

    Uses vendor plugin for request/response parsing and header building,
    and site plugin for job authorization and reporting.
    """
    rc = routes_config

    # Skip auth for public routes
    if request.url.path in rc.public_routes:
        return await call_next(request)

    # Check deprecated routes
    for dep_route in rc.deprecated_routes:
        if request.url.path.startswith(dep_route):
            return JSONResponse(
                status_code=410,
                content={
                    "detail": "This endpoint is deprecated. Please use the current server endpoints.",
                    "suggestion": "Check your client version and update it to use the latest endpoints.",
                },
            )

    path = request.url.path

    # Maintenance mode
    if settings.MIDDLEWARE_MODE == "maintenance":
        return Response(
            content="Service in maintenance mode",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="text/plain",
        )

    # Whitelist enforcement via vendor-provided routes
    if not role_checker.is_route_configured(path, request.method):
        logger.warning("Forbidden path access attempt: %s %s", request.method, path)
        return JSONResponse(status_code=403, content={"detail": "Forbidden: path not allowed"})

    logger.info("Request: %s %s", request.method, request.url.path)
    try:
        user = await get_current_user(request)
    except HTTPException as e:
        logger.warning(
            "Auth failed for %s %s: %s (status=%s)",
            request.method, request.url.path, e.detail, e.status_code,
        )
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})

    # Role-based access control
    if not role_checker.check(path, request.method, user):
        return JSONResponse(status_code=403, content={"detail": "Forbidden: insufficient role"})

    request_body = await request.body()

    # Log request body at DEBUG level
    try:
        _body_text = request_body.decode("utf-8")
    except Exception:
        _body_text = repr(request_body)
    _max_len = 2000
    _log_body = (
        (_body_text[:_max_len] + "...(truncated)") if len(_body_text) > _max_len else _body_text
    )
    logger.debug("Request body: %s", _log_body)

    # Parse submission details via vendor plugin
    submission = vendor_plugin.parse_submission_request(request_body, path)

    # Determine if this route should be intercepted for logging and authorization
    should_auth_and_log = False
    for log_path, methods in rc.logged_routes.items():
        if path.startswith(log_path) and request.method in methods:
            should_auth_and_log = True
            break

    # Job authorization via site plugin (production mode only)
    if settings.MIDDLEWARE_MODE == "production" and should_auth_and_log:
        username_val = getattr(user, "username", None)
        logger.info("Checking job authorization for user: %s", username_val)

        if site_plugin:
            auth_headers = {
                k: v
                for k, v in request.headers.items()
                if k.lower() not in ["host", "content-length"]
            }

            auth_result = await site_plugin.authorize_job(
                username=username_val,
                project_name=submission.project,
                extra_headers=auth_headers,
                timeout=float(settings.UPSTREAM_TIMEOUT),
            )

            if not auth_result.is_authorized:
                logger.warning("Job authorization denied for %s", username_val)
                return JSONResponse(
                    status_code=auth_result.status_code or 403, content=auth_result.error_detail
                )

    # Concurrency limiting (production mode only)
    reservation_result = None
    if settings.MIDDLEWARE_MODE == "production" and should_auth_and_log:
        username_val = getattr(user, "username", None)
        logger.info("Extracted shots from body: %s", submission.shots)
        logger.info("Counted circuits in body: %s", submission.circuits)

        concurrency_limiter_inst = getattr(app.state, "concurrency_limiter", None)
        if concurrency_limiter_inst and username_val:
            reservation_result = concurrency_limiter_inst.try_reserve(
                username=username_val,
                shots=submission.shots,
                circuits=submission.circuits,
                job_type=submission.job_type,
            )

            if not reservation_result.allowed:
                logger.warning("User %s exceeded concurrent submission limit", username_val)
                if submission.job_type == "sweep":
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": f"Max concurrent sweeps reached ({settings.MAX_CONCURRENT_SWEEPS}) for user {username_val}"
                        },
                    )
                else:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": f"Max concurrent shots reached ({settings.MAX_CONCURRENT_SHOTS}) for user {username_val}"
                        },
                    )

            logger.info(
                "Active shots for user %s: %s", username_val, reservation_result.shots_after
            )

    # Proxy to upstream
    try:
        upstream_url = f"{settings.MACHINE_URL}{request.url.path}"
        logger.debug("Proxying request to upstream URL: %s", upstream_url)

        # Build headers: strip hop-by-hop and client auth, inject vendor auth
        proxy_headers = {
            k: v for k, v in request.headers.items() if k.lower() not in ["host", "authorization"]
        }
        proxy_headers = vendor_plugin.build_upstream_headers(proxy_headers)

        upstream_response = await app.state.http_client.request(
            method=request.method,
            url=upstream_url,
            headers=proxy_headers,
            content=request_body,
            timeout=settings.UPSTREAM_TIMEOUT,
            follow_redirects=True,
        )

        response_body = upstream_response.text

        # On successful submission of a logged route, capture and report
        if (
            settings.MIDDLEWARE_MODE in ["production", "reporting"]
            and should_auth_and_log
            and upstream_response.status_code in range(200, 300)
        ):
            # Parse response via vendor plugin
            sub_result = vendor_plugin.parse_submission_response(response_body)
            jobid = sub_result.job_id
            artifact_types = sub_result.artifact_types

            if jobid and artifact_types:
                logger.info("Upstream returned initial artifacts: %s", artifact_types)
                username_val = getattr(user, "username", None)
                for atype in artifact_types:
                    try:
                        art_url = (
                            f"{settings.MACHINE_URL}{vendor_plugin.get_artifact_url(jobid, atype)}"
                        )
                        art_resp = await app.state.http_client.get(
                            art_url,
                            headers=proxy_headers,
                            timeout=settings.UPSTREAM_TIMEOUT,
                            follow_redirects=True,
                        )
                        if art_resp.status_code == 200 and username_val:
                            try:
                                url = upload_artifact_from_response(
                                    uploader, username_val, jobid, atype, art_resp
                                )
                                logger.info("Uploaded initial artifact %s: %s", atype, url)
                            except Exception as e:
                                logger.error("Failed to upload initial artifact %s: %s", atype, e)
                        else:
                            logger.warning(
                                "Could not fetch initial artifact %s, status %s",
                                atype,
                                art_resp.status_code,
                            )
                    except Exception as e:
                        logger.error("Exception fetching initial artifact %s: %s", atype, e)

            logger.info("Upstream response: %s; %s", upstream_response.status_code, response_body)

            submitted_datetime = (
                datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
            )
            username_val = getattr(user, "username", None)
            submitted_circuit_url = upload_submitted_circuit(
                uploader, request_body, username_val, jobid
            )
            logger.info("Submitted circuit URL: %s", submitted_circuit_url)
            log_job(
                jobid=jobid,
                project_name=submission.project,
                username=username_val,
                status="submitted",
                execution_start="",
                execution_end="",
                submitted_datetime=submitted_datetime,
                submitted_circuit=submitted_circuit_url,
                results="",
                job_type=submission.job_type,
                shots=submission.shots if submission.shots is not None else 0,
                circuits_count=submission.circuits,
            )

            # Send initial job report via site plugin
            if site_plugin and getattr(user, "username", None):
                job_report_payload = {
                    "jobid": jobid,
                    "username": getattr(user, "username", None),
                    "status": "submitted",
                    "submitted_datetime": submitted_datetime,
                    "submitted_circuit": submitted_circuit_url,
                    "job_type": submission.job_type,
                }
                if submission.project is not None:
                    job_report_payload["project_name"] = submission.project
                try:
                    report_result = await site_plugin.send_initial_report(job_report_payload)
                    if not report_result.success:
                        logger.error("jobReport failed: %s", report_result.error_detail)
                        return JSONResponse(
                            status_code=502, content={"detail": "JobReport API error"}
                        )
                    logger.info("jobReport request: %s", job_report_payload)
                    logger.info(
                        "jobReport response: %s %s",
                        report_result.status_code,
                        report_result.response_text,
                    )
                except Exception as e:
                    logger.error("Failed to send jobReport: %s", e)
                    return JSONResponse(
                        status_code=502, content={"detail": f"JobReport API error: {e!s}"}
                    )
        else:
            # Rollback counters on failure
            if reservation_result and reservation_result.allowed:
                username_val = getattr(user, "username", None)
                concurrency_limiter_inst = getattr(app.state, "concurrency_limiter", None)
                if concurrency_limiter_inst and username_val:
                    concurrency_limiter_inst.rollback(
                        username_val, reservation_result.pre_increment_id
                    )
            logger.debug("Upstream response status: %s", upstream_response.status_code)
            logger.debug("Upstream response body: %s", response_body)

        # Filter and return upstream response
        response_headers = filter_response_headers(upstream_response.headers)

        if "application/json" in upstream_response.headers.get("content-type", "").lower():
            try:
                content = upstream_response.json()
                return JSONResponse(
                    content=content,
                    status_code=upstream_response.status_code,
                    headers=response_headers,
                )
            except Exception as e:
                logger.error("Failed to parse upstream response as JSON: %s", e)
                return build_raw_response(upstream_response)
        else:
            return build_raw_response(upstream_response)

    except httpx.RequestError as e:
        # Rollback counter on request exception
        if reservation_result and reservation_result.allowed:
            username_val = getattr(user, "username", None)
            concurrency_limiter_inst = getattr(app.state, "concurrency_limiter", None)
            if concurrency_limiter_inst and username_val:
                concurrency_limiter_inst.rollback(username_val, reservation_result.pre_increment_id)
        logger.error("Upstream API error: %s", str(e), exc_info=True)
        return JSONResponse(status_code=502, content={"detail": f"Upstream API error: {e!s}"})


# Public endpoint to show some config values
@app.get("/proxy-config", include_in_schema=False)
async def config_status():
    rc = getattr(app.state, "routes_config", None)
    role_routes = rc.role_routes if rc else {}
    role_routes_json = [
        {"path": path, "method": method, "roles": roles}
        for (path, method), roles in role_routes.items()
    ]
    return JSONResponse(
        {
            "MIDDLEWARE_MODE": settings.MIDDLEWARE_MODE,
            "MAX_CONCURRENT_SHOTS": settings.MAX_CONCURRENT_SHOTS,
            "ROLE_ROUTES": role_routes_json,
        }
    )


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/api/v1/quantum-computers/default/health", include_in_schema=False)
async def quantum_computer_health(request: Request):
    """Proxy health check request to the upstream server."""
    vp = getattr(app.state, "vendor_plugin", None)
    health_path = vp.get_health_endpoint() if vp else "/api/v1/quantum-computers/default/health"
    upstream_url = f"{settings.MACHINE_URL}{health_path}"

    proxy_headers = {k: v for k, v in request.headers.items() if k.lower() != "authorization"}
    if vp:
        proxy_headers = vp.build_upstream_headers(proxy_headers)

    try:
        upstream_response = await app.state.http_client.get(
            url=upstream_url,
            headers=proxy_headers,
            timeout=settings.UPSTREAM_TIMEOUT,
            follow_redirects=True,
        )
        return build_json_response(upstream_response)
    except httpx.RequestError as e:
        logger.error("Upstream health check error: %s", str(e), exc_info=True)
        return JSONResponse(
            status_code=502, content={"detail": f"Upstream health check error: {e!s}"}
        )


@app.get("/metrics")
async def metrics_endpoint():
    """Expose Prometheus metrics for scraping."""
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
