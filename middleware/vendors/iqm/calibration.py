"""IQM-specific calibration polling and artifact enrichment.

Handles periodic polling of IQM calibration runs and uploading reports
to MinIO, as well as enriching job artifacts with calibration data.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


def ensure_calibration_table(conn) -> None:
    """Ensure the ``calibration_runs_processed`` table exists (idempotent)."""
    with conn.cursor() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS calibration_runs_processed (
                run_id UUID PRIMARY KEY,
                calibration_set_id UUID,
                processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
            """
        )
        conn.commit()


def enrich_artifact_locations_with_calibration(
    uploader: Any,
    job_json: dict[str, Any],
    username: str,
    jobid: str,
    machine_url: str,
    headers: dict[str, str],
    timeout: float,
    verify_tls: bool,
    minio_server_url: str = "",
    bucket_name: str = "",
) -> dict[str, str]:
    """Return a dict with calibration-related artifact entries when available."""
    artifact_fragment: dict[str, str] = {}
    calibration_id = job_json.get("compilation", {}).get("calibration_set_id")
    if not calibration_id:
        return artifact_fragment

    cal_url = f"{minio_server_url}/{bucket_name}/calibration/{calibration_id}_report.zip"
    artifact_fragment["calibration_report"] = cal_url

    qc_id = None
    try:
        qc_id = job_json.get("qc", {}).get("id")
    except Exception:
        qc_id = None

    if qc_id:
        metrics_url_endpoint = (
            f"{machine_url}/api/v1/calibration-sets/{qc_id}/{calibration_id}/metrics"
        )
        try:
            resp = requests.get(
                metrics_url_endpoint, headers=headers, timeout=timeout, verify=verify_tls
            )
            if resp.status_code == 200:
                try:
                    metrics_json = resp.json()
                except Exception:
                    metrics_json = None

                try:
                    metrics_object_name = f"{username}/{jobid}/calibration_metrics.json"
                    uploaded_metrics_url = uploader.upload_json(
                        metrics_json if metrics_json is not None else resp.text,
                        metrics_object_name,
                    )
                    artifact_fragment["calibration_metrics"] = uploaded_metrics_url
                except Exception as e:
                    logger.exception(
                        "Failed to upload calibration metrics for job %s: %s", jobid, e
                    )
        except Exception as e:
            logger.exception(
                "Error fetching calibration metrics for calibration %s: %s",
                calibration_id,
                e,
            )

    return artifact_fragment


def process_calibration_runs(
    machine_url: str,
    headers: dict[str, str],
    uploader: Any,
    db_init_fn: Any,
    timeout: float,
    verify_tls: bool,
) -> None:
    """Poll IQM for calibration runs and upload any new reports."""
    conn = None
    cursor = None
    try:
        conn = db_init_fn()
        ensure_calibration_table(conn)
        cursor = conn.cursor()

        cursor.execute("SELECT run_id::text FROM calibration_runs_processed")
        processed_ids = {row[0] for row in cursor.fetchall()}

        url = f"{machine_url}/cocos/api/v4/calibration/runs"
        logger.info("Calibration: fetching runs from %s", url)
        resp = requests.get(url, headers=headers, timeout=timeout, verify=verify_tls)
        resp.raise_for_status()
        payload = resp.json()
        runs = payload.get("runs", {}) or {}
        logger.info("Calibration: fetched %d runs", len(runs))

        new_processed = 0
        for run_id, info in runs.items():
            try:
                status = info.get("status")
                result = info.get("result") or {}
                success = result.get("success") is True
                calibration_set_id = result.get("calibration_set_id")

                if not run_id or not calibration_set_id:
                    continue
                if run_id in processed_ids:
                    continue
                if status != "ready" or not success:
                    continue

                report_url = f"{machine_url}/cocos/api/v4/calibration/runs/{run_id}/report"
                logger.info("Calibration: downloading report from %s", report_url)
                r = requests.get(report_url, headers=headers, timeout=timeout, verify=verify_tls)
                if r.status_code != 200:
                    logger.warning(
                        "Calibration: report not available for run %s, status %s",
                        run_id,
                        r.status_code,
                    )
                    continue

                content_type = r.headers.get("Content-Type", "application/zip")
                data = r.content

                object_name = f"calibration/{calibration_set_id}_report.zip"
                uploader.upload_bytes(data, object_name, content_type=content_type)

                cursor.execute(
                    "INSERT INTO calibration_runs_processed (run_id, calibration_set_id) "
                    "VALUES (%s, %s) ON CONFLICT (run_id) DO NOTHING",
                    (run_id, calibration_set_id),
                )
                conn.commit()
                new_processed += 1
                logger.info("Calibration: uploaded report for run %s as %s", run_id, object_name)
            except Exception as e:
                conn.rollback()
                logger.exception("Calibration: error processing run %s: %s", run_id, e)

        if new_processed == 0:
            logger.info("Calibration: no new reports to process")
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
