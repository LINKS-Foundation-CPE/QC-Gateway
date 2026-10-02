"""IQM-specific calibration polling and artifact enrichment.

Handles periodic polling of IQM calibration runs and uploading reports
to MinIO, publishing a day-by-day index of them, and enriching job artifacts
with calibration data.
"""

from __future__ import annotations

import html
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

INDEX_JSON = "calibration/index.json"
INDEX_HTML = "calibration/index.html"

# Runs whose report the machine would not serve, already reported in this
# process. The mock answers 500 for every run older than a few months, and each
# poll retries them — rightly, since a report can become available — but saying
# so every hour, forever, is how a log stops being read.
_unavailable_reported: set[str] = set()


def report_object_name(calibration_set_id: str) -> str:
    """Where a calibration set's report lives in the bucket.

    Jobs link to this path by calibration set id, before the report is known to
    exist, so it is fixed: changing it breaks every job page already written.
    """
    return f"calibration/{calibration_set_id}_report.zip"


def ensure_calibration_table(conn) -> None:
    """Ensure the ``calibration_runs_processed`` table exists (idempotent).

    ``run_timestamp`` and ``run_info`` came later, for the day-by-day index:
    older rows have neither, which is why the columns are nullable and are
    backfilled from the machine's run listing rather than required.
    """
    with conn.cursor() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS calibration_runs_processed (
                run_id UUID PRIMARY KEY,
                calibration_set_id UUID,
                processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
            ALTER TABLE calibration_runs_processed
                ADD COLUMN IF NOT EXISTS run_timestamp TIMESTAMP WITH TIME ZONE,
                ADD COLUMN IF NOT EXISTS run_info JSONB,
                ADD COLUMN IF NOT EXISTS report_bytes BIGINT;
            """
        )
        conn.commit()


def run_timestamp(info: dict[str, Any]) -> tuple[datetime | None, str | None]:
    """When a calibration run happened, and which field said so.

    The listing carries ``start_time`` and ``end_time`` as naive ISO 8601. They
    are UTC: a report's ``Last-Modified`` header (always GMT) equals its run's
    ``end_time`` to the second. ``end_time`` is preferred — the report is what
    the run produced, so it is dated by when it finished.
    """
    for key in ("end_time", "start_time"):
        value = info.get(key)
        if not value:
            continue
        try:
            ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
        return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)), key
    return None, None


@dataclass(frozen=True)
class RunPlan:
    """What one poll has to do, decided before touching the machine or the DB."""

    to_fetch: list[tuple[str, str, dict[str, Any]]]
    """(run_id, calibration_set_id, info) for finished runs with no report yet."""
    to_backfill: list[tuple[str, dict[str, Any]]]
    """(run_id, info) for runs already fetched whose date was never recorded."""


def plan_runs(runs: dict[str, Any], processed: dict[str, bool]) -> RunPlan:
    """Split the machine's run listing into reports to fetch and dates to record.

    ``processed`` maps each run id already in the table to whether its date is
    known. Only successful runs with a calibration set are fetchable, exactly as
    before this split existed.
    """
    to_fetch: list[tuple[str, str, dict[str, Any]]] = []
    to_backfill: list[tuple[str, dict[str, Any]]] = []
    for run_id, info in runs.items():
        if not run_id or not isinstance(info, dict):
            continue
        if run_id in processed:
            if not processed[run_id]:
                to_backfill.append((run_id, info))
            continue
        result = info.get("result") or {}
        calibration_set_id = result.get("calibration_set_id")
        if not calibration_set_id:
            continue
        if info.get("status") != "ready" or result.get("success") is not True:
            continue
        to_fetch.append((run_id, calibration_set_id, info))
    return RunPlan(to_fetch=to_fetch, to_backfill=to_backfill)


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

    cal_url = f"{minio_server_url}/{bucket_name}/{report_object_name(calibration_id)}"
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
    """Poll IQM for calibration runs, upload any new reports, republish the index.

    ``headers`` must carry a token the calibration endpoints accept. On current
    machines that is an administrator token, not the device token the job path
    uses — see ``IQMVendorPlugin.build_calibration_headers``.
    """
    conn = None
    cursor = None
    try:
        conn = db_init_fn()
        ensure_calibration_table(conn)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT run_id::text, run_timestamp IS NOT NULL FROM calibration_runs_processed"
        )
        processed = {row[0]: bool(row[1]) for row in cursor.fetchall()}

        url = f"{machine_url}/cocos/api/v4/calibration/runs"
        logger.info("Calibration: fetching runs from %s", url)
        resp = requests.get(url, headers=headers, timeout=timeout, verify=verify_tls)
        resp.raise_for_status()
        runs = resp.json().get("runs", {}) or {}
        plan = plan_runs(runs, processed)
        logger.info(
            "Calibration: %d runs listed, %d new reports, %d dates to backfill",
            len(runs),
            len(plan.to_fetch),
            len(plan.to_backfill),
        )

        unplaced = _backfill(cursor, conn, plan.to_backfill)
        for run_id, calibration_set_id, info in plan.to_fetch:
            stored = _fetch_report(
                cursor,
                conn,
                uploader,
                machine_url,
                headers,
                timeout,
                verify_tls,
                run_id,
                calibration_set_id,
                info,
            )
            if stored and run_timestamp(info)[0] is None:
                unplaced.append(run_id)

        if unplaced:
            # Worth saying loudly once: every run lacking a date lands on the day
            # it was fetched, which for a backlog is the same day for all of them.
            sample = runs.get(unplaced[0]) or {}
            logger.warning(
                "Calibration: %d run(s) carry no recognisable timestamp; placed by fetch "
                "time instead. Keys on the first one: %s",
                len(unplaced),
                sorted(sample) + sorted((sample.get("result") or {}).keys()),
            )

        publish_index(cursor, uploader)
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


def _backfill(cursor: Any, conn: Any, runs: list[tuple[str, dict[str, Any]]]) -> list[str]:
    """Record the date of runs fetched before dates were kept. Returns the undatable."""
    unplaced = []
    for run_id, info in runs:
        ts, _ = run_timestamp(info)
        if ts is None:
            unplaced.append(run_id)
        try:
            cursor.execute(
                "UPDATE calibration_runs_processed "
                "SET run_timestamp = %s, run_info = %s WHERE run_id = %s",
                (ts, json.dumps(info), run_id),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.exception("Calibration: could not backfill run %s: %s", run_id, e)
    return unplaced


def _fetch_report(
    cursor: Any,
    conn: Any,
    uploader: Any,
    machine_url: str,
    headers: dict[str, str],
    timeout: float,
    verify_tls: bool,
    run_id: str,
    calibration_set_id: str,
    info: dict[str, Any],
) -> bool:
    """Download one run's report, store it, record it. False if it was not stored."""
    try:
        report_url = f"{machine_url}/cocos/api/v4/calibration/runs/{run_id}/report"
        logger.info("Calibration: downloading report from %s", report_url)
        r = requests.get(report_url, headers=headers, timeout=timeout, verify=verify_tls)
        if r.status_code != 200:
            log = logger.debug if run_id in _unavailable_reported else logger.warning
            log(
                "Calibration: report not available for run %s, status %s "
                "(retried every poll, reported once)",
                run_id,
                r.status_code,
            )
            _unavailable_reported.add(run_id)
            return False

        data = r.content
        object_name = report_object_name(calibration_set_id)
        uploader.upload_bytes(
            data, object_name, content_type=r.headers.get("Content-Type", "application/zip")
        )
        cursor.execute(
            "INSERT INTO calibration_runs_processed "
            "(run_id, calibration_set_id, run_timestamp, run_info, report_bytes) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (run_id) DO NOTHING",
            (run_id, calibration_set_id, run_timestamp(info)[0], json.dumps(info), len(data)),
        )
        conn.commit()
        logger.info("Calibration: uploaded report for run %s as %s", run_id, object_name)
        return True
    except Exception as e:
        conn.rollback()
        logger.exception("Calibration: error processing run %s: %s", run_id, e)
        return False


# ── the day-by-day index ─────────────────────────────────────────────────────


def _index_zone() -> tuple[tzinfo, str]:
    """The zone days are cut in: the deployment's own ``TZ``, else UTC.

    A calibration at 00:30 in Turin is on the previous day in UTC, and the
    people reading this page are in Turin; the page names the zone it uses.
    """
    name = os.environ.get("TZ") or "UTC"
    try:
        return ZoneInfo(name), name
    except Exception:
        return UTC, "UTC"


def build_manifest(
    rows: list[tuple[Any, ...]], base_url: str, zone: tzinfo, zone_name: str
) -> dict[str, Any]:
    """The index as data: one entry per report, newest first.

    ``rows`` are ``(run_id, calibration_set_id, run_timestamp, processed_at,
    report_bytes)``. A run whose own time is unknown is placed at the time its
    report was fetched and marked ``approximate``, rather than left out: the
    report is still there to download.
    """
    reports = []
    for run_id, calibration_set_id, ts, processed_at, size in rows:
        when = ts or processed_at
        if when is None or not calibration_set_id:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        local = when.astimezone(zone)
        reports.append(
            {
                "calibration_set_id": str(calibration_set_id),
                "run_id": str(run_id),
                "timestamp": when.astimezone(UTC).isoformat(),
                "date": local.date().isoformat(),
                "time": local.strftime("%H:%M"),
                "approximate": ts is None,
                "bytes": int(size) if size is not None else None,
                "url": f"{base_url}/{report_object_name(str(calibration_set_id))}",
            }
        )
    reports.sort(key=lambda r: r["timestamp"], reverse=True)
    return {
        "generated": datetime.now(UTC).isoformat(),
        "timezone": zone_name,
        "reports": reports,
    }


def _size(n: int | None) -> str:
    if n is None:
        return ""
    value = float(n)
    for unit in ("B", "kB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""


def render_index(manifest: dict[str, Any]) -> str:
    """The index as a page: reports grouped by day, newest day first.

    Self-contained on purpose — no scripts, no fonts, nothing fetched — because
    it is served straight out of the object store, and a page there that
    depends on anything else breaks in ways nobody is watching for.
    """
    esc = html.escape
    days: dict[str, list[dict[str, Any]]] = {}
    for r in manifest["reports"]:
        days.setdefault(r["date"], []).append(r)

    body = []
    for day, items in days.items():
        pretty = datetime.fromisoformat(day).strftime("%A %-d %B %Y")
        body.append(f'<tr class="day"><th colspan="4">{esc(pretty)}</th></tr>')
        for r in items:
            note = (
                ' <span class="approx" title="The machine gave no time for this run; this is '
                'when its report was fetched.">approx.</span>'
                if r["approximate"]
                else ""
            )
            body.append(
                "<tr>"
                f'<td class="time">{esc(r["time"])}{note}</td>'
                f'<td class="id"><code>{esc(r["calibration_set_id"])}</code></td>'
                f'<td class="size">{esc(_size(r["bytes"]))}</td>'
                f'<td><a href="{esc(r["url"])}" download>Download</a></td>'
                "</tr>"
            )
    if not body:
        body.append('<tr><td colspan="4" class="empty">No calibration reports yet.</td></tr>')

    generated = datetime.fromisoformat(manifest["generated"]).strftime("%Y-%m-%d %H:%M UTC")
    count = len(manifest["reports"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calibration reports</title>
<style>
  :root {{ --ink:#1e2a38; --muted:#5a6a7e; --rule:#d0dae8; --band:#f3f6fa; --accent:#1f5fae; --bg:#fff; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --ink:#e6edf6; --muted:#9fb0c4; --rule:#2c3a4d; --band:#18222f; --accent:#7fb2f0; --bg:#0f1720; }}
  }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.5 "Segoe UI", system-ui, -apple-system, sans-serif; }}
  main {{ max-width:52rem; margin:0 auto; padding:2rem 1rem 3rem; }}
  h1 {{ font-size:1.5rem; margin:0 0 .25rem; }}
  p.lede {{ color:var(--muted); margin:0 0 1.5rem; }}
  table {{ width:100%; border-collapse:collapse; }}
  tr.day th {{ text-align:left; font-weight:600; padding:1.1rem .5rem .35rem;
              border-bottom:1px solid var(--rule); }}
  td {{ padding:.4rem .5rem; border-bottom:1px solid var(--band); vertical-align:baseline; }}
  td.time {{ width:7rem; font-variant-numeric:tabular-nums; white-space:nowrap; }}
  td.id code {{ font-size:.85rem; color:var(--muted); word-break:break-all; }}
  td.size {{ width:6rem; text-align:right; color:var(--muted); font-variant-numeric:tabular-nums; }}
  td.empty {{ color:var(--muted); padding:1.5rem .5rem; }}
  a {{ color:var(--accent); }}
  .approx {{ font-size:.75rem; color:var(--muted); border:1px solid var(--rule);
            border-radius:3px; padding:0 .25rem; margin-left:.25rem; cursor:help; }}
  footer {{ margin-top:2rem; color:var(--muted); font-size:.85rem; }}
</style>
</head>
<body>
<main>
<h1>Calibration reports</h1>
<p class="lede">{count} report{"" if count == 1 else "s"}, newest first. Times are
{esc(manifest["timezone"])}. Each report is the archive the machine produced for
that calibration set; a job's own page links the one it ran against.</p>
<table>
<tbody>
{chr(10).join(body)}
</tbody>
</table>
<footer>Updated {esc(generated)} · <a href="index.json">index.json</a></footer>
</main>
</body>
</html>
"""


def publish_index(cursor: Any, uploader: Any) -> str:
    """Rewrite ``calibration/index.json`` and ``calibration/index.html``.

    Regenerated from the table on every poll rather than appended to, so it can
    never drift from what is actually stored, and a hand-deleted report comes
    back correct on the next poll.
    """
    cursor.execute(
        "SELECT run_id::text, calibration_set_id::text, run_timestamp, processed_at, "
        "report_bytes FROM calibration_runs_processed"
    )
    zone, zone_name = _index_zone()
    base = f"{uploader.minio_server_url}/{uploader.bucket_name}"
    manifest = build_manifest(cursor.fetchall(), base, zone, zone_name)
    uploader.upload_json(manifest, INDEX_JSON)
    url = uploader.upload_bytes(
        render_index(manifest).encode("utf-8"), INDEX_HTML, content_type="text/html; charset=utf-8"
    )
    logger.info(
        "Calibration: index republished with %d reports at %s", len(manifest["reports"]), url
    )
    return url
