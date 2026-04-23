#!/usr/bin/env python3

"""Background job reporter — uploads artifacts and reports jobs.

This module runs as a background worker that reconciles locally-tracked jobs
with the upstream quantum computer server and publishes job artifacts and
calibration reports to MinIO.

Vendor-specific logic (e.g., how to query job status, which terminal statuses
exist, how to fetch artifacts) is delegated to the vendor plugin.
Site-specific logic (e.g., how to report jobs, how to build results URLs)
is delegated to the site plugin.

Main responsibilities:
- Poll the local ``jobs`` DB table and fetch authoritative job details
  from the upstream server via the vendor plugin.
- For terminal jobs: upload artifacts, timeline and payload to MinIO and
  send job reports via the site plugin.
- Decrement per-user Redis counters (jobs/shots) in an idempotent manner.
- Periodically run vendor-specific calibration processing.

Reliability and design notes:
- Best-effort behavior: network/storage errors are logged and do not cause
  permanent data loss; main loop implements exponential backoff on repeated
  failures.
- Idempotency is achieved using Redis sets and a DB table.

Public functions:
- process_once(): run a single reconciliation pass.
- main(): continuous loop with graceful shutdown and backoff.
"""

import logging
import signal
import sys
import time

import requests

from middleware.artifacts import upload_artifact_from_response, upload_links_html, upload_timeline
from middleware.config import Settings
from middleware.db import init_db
from middleware.minio import S3Uploader
from middleware.plugins.loader import load_site_plugin, load_vendor_plugin

requests.packages.urllib3.disable_warnings()

logger = logging.getLogger(__name__)

settings = Settings()

# Reuse centralized application settings
MACHINE_URL = settings.MACHINE_URL
MINIO_SERVER_URL = settings.MINIO_SERVER_URL
BUCKET_NAME = settings.BUCKET_NAME
APP_USER = settings.APP_USER
APP_PASSWORD = settings.APP_PASSWORD

REDIS_HOST = settings.REDIS_HOST
REDIS_PORT = int(settings.REDIS_PORT)
REDIS_DB = int(settings.REDIS_DB)
REDIS_PASSWORD = settings.REDIS_PASSWORD or None

LOOP_SLEEP_SECONDS = int(settings.JOB_REPORTER_INTERVAL)
HTTP_TIMEOUT = float(settings.JOB_REPORTER_HTTP_TIMEOUT or settings.UPSTREAM_TIMEOUT)
VERIFY_TLS = (
    settings.JOB_REPORTER_VERIFY_TLS
    if settings.JOB_REPORTER_VERIFY_TLS is not None
    else settings.VERIFY_UPSTREAM_SSL
)
MAX_CONSECUTIVE_ERRORS = int(settings.JOB_REPORTER_MAX_ERRORS)
BACKOFF_FACTOR = float(settings.JOB_REPORTER_BACKOFF_FACTOR)
MAX_BACKOFF_SECONDS = int(settings.JOB_REPORTER_MAX_BACKOFF)

_should_terminate = False


def _sigterm_handler(signum, frame):
    global _should_terminate
    logger.info("Received signal %s, shutting down gracefully...", signum)
    _should_terminate = True


signal.signal(signal.SIGTERM, _sigterm_handler)
signal.signal(signal.SIGINT, _sigterm_handler)

from middleware.job_counters import decrement_user_counters, get_redis_client


def process_once(vendor_plugin, site_plugin):
    """Run a single reconciliation cycle for jobs.

    Uses the vendor plugin to query job status and classify artifacts,
    and the site plugin to report results.
    """
    logger.info("Starting process_once")
    uploader = S3Uploader(
        minio_server_url=MINIO_SERVER_URL,
        bucket_name=BUCKET_NAME,
        app_user=APP_USER,
        app_password=APP_PASSWORD,
    )
    logger.info("S3Uploader initialized")

    r = get_redis_client()
    if r:
        logger.info("Redis client connected")
    else:
        logger.warning("Redis client not available")

    # Build machine headers via vendor plugin
    machine_headers = vendor_plugin.build_upstream_headers({})
    terminal_statuses = vendor_plugin.get_terminal_statuses()

    conn = None
    cursor = None
    try:
        conn = init_db()
        logger.info("Database connection initialized")
        cursor = conn.cursor()

        cursor.execute(
            "SELECT jobid, project_name, username, status, submitted_datetime, submitted_circuit, job_type, circuits_count, shots FROM jobs WHERE status NOT IN (%s, %s)",
            ("ready", "failed"),
        )
        jobs = cursor.fetchall()
        logger.info("Found %d jobs to check", len(jobs))

        for job in jobs:
            (
                jobid,
                project_name,
                username,
                status,
                submitted_datetime,
                submitted_circuit,
                job_type,
                circuits_count,
                shots_val,
            ) = job
            try:
                logger.info("Processing job %s", jobid)

                # Fetch job details via vendor plugin
                logger.info("Fetching job details for %s", jobid)
                job_status = vendor_plugin.get_job_status(
                    job_id=jobid,
                    machine_url=MACHINE_URL,
                    headers=machine_headers,
                    timeout=HTTP_TIMEOUT,
                    verify_tls=VERIFY_TLS,
                )
                logger.info("Job details fetched for %s", jobid)
                new_status = job_status.status

                if job_status.calibration_id:
                    logger.info("Found calibration_set_id: %s", job_status.calibration_id)

                if new_status in terminal_statuses:
                    # Decrement counter idempotently
                    if r is not None and username:
                        try:
                            processed_set = "jobs:terminal:processed"
                            added = r.sadd(processed_set, jobid)
                            try:
                                r.expire(processed_set, 3 * 24 * 3600)
                            except Exception:
                                pass
                            if added == 1:
                                decrement_user_counters(r, username, circuits_count, shots_val)
                                logger.info(
                                    "Decremented active counter for %s (job %s)", username, jobid
                                )
                        except Exception:
                            logger.exception("Redis decrement/idempotency error for %s", username)

                    # Upload timeline
                    artifact_locations = {}
                    try:
                        logger.info("Uploading timeline for job %s", jobid)
                        timeline_url = upload_timeline(
                            uploader, username, jobid, job_status.timeline
                        )
                        artifact_locations["timeline"] = timeline_url
                        logger.info("Uploaded timeline for job %s", jobid)
                    except Exception:
                        logger.exception("Failed to upload timeline for job %s", jobid)

                    # Extract execution timestamps from timeline
                    execution_start = job_status.execution_start
                    execution_end = job_status.execution_end

                    if not execution_start or not execution_end:
                        if new_status == "failed":
                            logger.warning(
                                "Missing execution timestamps for failed job %s, proceeding", jobid
                            )
                        else:
                            logger.warning(
                                "Missing execution timestamps for job %s; skipping report", jobid
                            )
                            continue

                    # Classify artifacts via vendor plugin
                    all_artifact_types = list(job_status.available_artifacts)
                    classification = vendor_plugin.classify_artifacts(
                        new_status, all_artifact_types
                    )
                    artifact_types = classification.types_to_fetch
                    if classification.include_payload:
                        artifact_types = [*list(artifact_types), "payload"]

                    logger.debug("Artifact types to process: %s", artifact_types)

                    for atype in artifact_types:
                        logger.info("Fetching artifact %s for job %s", atype, jobid)

                        if atype == "payload":
                            artifact_url = f"{MACHINE_URL}{vendor_plugin.get_payload_url(jobid)}"
                        else:
                            artifact_url = (
                                f"{MACHINE_URL}{vendor_plugin.get_artifact_url(jobid, atype)}"
                            )

                        resp = requests.get(
                            artifact_url,
                            headers=machine_headers,
                            timeout=HTTP_TIMEOUT,
                            verify=VERIFY_TLS,
                        )

                        logger.debug("Response status for artifact %s: %s", atype, resp.status_code)
                        if resp.status_code == 200:
                            # Enrich with calibration data via vendor plugin
                            if job_status.calibration_id:
                                artifact_locations.update(
                                    vendor_plugin.enrich_artifacts_with_calibration(
                                        uploader=uploader,
                                        job_json=job_status.raw_json,
                                        username=username,
                                        jobid=jobid,
                                        machine_url=MACHINE_URL,
                                        headers=machine_headers,
                                        timeout=HTTP_TIMEOUT,
                                        verify_tls=VERIFY_TLS,
                                    )
                                )

                            logger.info("Uploading artifact %s for job %s", atype, jobid)
                            results_url = upload_artifact_from_response(
                                uploader, username, jobid, atype, resp
                            )
                            artifact_locations[atype] = results_url
                            logger.info("Uploaded artifact %s for job %s", atype, jobid)
                        else:
                            logger.debug(
                                "Artifact %s not found for job %s, status %s",
                                atype,
                                jobid,
                                resp.status_code,
                            )

                    logger.info("Uploading links as HTML for job %s", jobid)
                    title = "Sweep job artifacts" if job_type == "sweep" else "Job artifacts"
                    index_url = upload_links_html(
                        uploader, username, jobid, artifact_locations, title=title
                    )

                    # Build results URL via site plugin
                    results_url = site_plugin.build_results_url(index_url, job_type)
                    logger.info("Uploaded links as HTML for job %s", jobid)

                    job_report_payload = {
                        "jobid": jobid,
                        "username": username,
                        "status": new_status,
                        "submitted_datetime": submitted_datetime,
                        "execution_start": execution_start,
                        "execution_end": execution_end,
                        "submitted_circuit": submitted_circuit,
                        "results": results_url,
                    }
                    if project_name:
                        job_report_payload["project_name"] = project_name
                    logger.info("Sending job report for %s", jobid)

                    # Report via site plugin
                    report_result = site_plugin.report_job_sync(jobid, job_report_payload)
                    logger.info(
                        "Job report response: %s %s",
                        report_result.method,
                        report_result.status_code,
                    )

                    if report_result.success:
                        logger.info("jobReport accepted for %s, deleting job from DB", jobid)
                        cursor.execute("DELETE FROM jobs WHERE jobid=%s", (jobid,))
                    else:
                        logger.warning(
                            "jobReport failed for %s, status %s: %s",
                            jobid,
                            report_result.status_code,
                            report_result.error_detail,
                        )

                elif new_status and new_status != status:
                    job_report_payload = {
                        "jobid": jobid,
                        "username": username,
                        "status": new_status,
                    }
                    if project_name:
                        job_report_payload["project_name"] = project_name

                    report_result = site_plugin.report_job_sync(jobid, job_report_payload)
                    if report_result.success:
                        cursor.execute(
                            "UPDATE jobs SET status=%s WHERE jobid=%s", (new_status, jobid)
                        )
                        logger.info("Updated status for job %s to %s", jobid, new_status)
                    else:
                        logger.warning(
                            "jobReport failed for %s, status %s", jobid, report_result.status_code
                        )
                conn.commit()
            except Exception as e:
                logger.exception("Error processing job %s: %s", jobid, e)
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def main():
    """Run the job-reporter in continuous mode with backoff and graceful shutdown."""
    logger.info("Starting job reporter in continuous mode...")

    # Load plugins
    vendor_plugin = load_vendor_plugin(settings)
    site_plugin = load_site_plugin(settings)

    # Build machine headers for calibration polling
    machine_headers = vendor_plugin.build_upstream_headers({})

    # Calibration polling cadence is owned by the vendor plugin.
    calibration_poll_seconds = vendor_plugin.get_calibration_poll_interval()

    consecutive_errors = 0
    last_calibration_poll = 0

    while not _should_terminate:
        loop_start = time.time()
        try:
            process_once(vendor_plugin, site_plugin)

            # Periodically run calibration polling via vendor plugin
            now = time.time()
            if now - last_calibration_poll >= calibration_poll_seconds:
                try:
                    cal_uploader = S3Uploader(
                        minio_server_url=MINIO_SERVER_URL,
                        bucket_name=BUCKET_NAME,
                        app_user=APP_USER,
                        app_password=APP_PASSWORD,
                    )
                    vendor_plugin.process_calibration_runs(
                        machine_url=MACHINE_URL,
                        headers=machine_headers,
                        uploader=cal_uploader,
                        db_init_fn=init_db,
                        timeout=HTTP_TIMEOUT,
                        verify_tls=VERIFY_TLS,
                    )
                except Exception as e:
                    logger.exception("Calibration: unhandled error in polling: %s", e)
                finally:
                    last_calibration_poll = now

            consecutive_errors = 0
            sleep_time = LOOP_SLEEP_SECONDS
        except Exception as e:
            consecutive_errors += 1
            sleep_time = min(
                int(LOOP_SLEEP_SECONDS * (BACKOFF_FACTOR ** min(consecutive_errors, 10))),
                MAX_BACKOFF_SECONDS,
            )
            logger.exception(
                "Unhandled error in loop: %s; consecutive_errors=%d, next_sleep=%ds",
                e,
                consecutive_errors,
                sleep_time,
            )
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                logger.error(
                    "Exceeded MAX_CONSECUTIVE_ERRORS (%d). Exiting for external restart.",
                    MAX_CONSECUTIVE_ERRORS,
                )
                sys.exit(1)

        elapsed = time.time() - loop_start
        remaining = max(0, sleep_time - int(elapsed))
        for _ in range(remaining):
            if _should_terminate:
                break
            time.sleep(1)

    logger.info("Job reporter stopped.")


if __name__ == "__main__":
    main()
