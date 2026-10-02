#!/usr/bin/env python3

"""Calibration poller — fetches calibration reports and publishes their index.

Runs as its own container, from the same image as the job reporter. It used to
be a branch inside the reporter's loop; it was split out when the machine began
demanding an administrator token for its calibration endpoints, so that:

- that token lives only in the process that needs it, never in the one that
  proxies user traffic or reports jobs;
- a calibration endpoint that refuses us is one quiet line in this container's
  log, not a stack trace every hour in the reporter's;
- the two cadences — seconds for jobs, an hour for calibration — stop sharing a
  loop, and a slow report download no longer holds up job finalisation.

What a poll does is vendor-specific and lives in the vendor plugin
(``process_calibration_runs``); this module only schedules it.
"""

import logging
import signal
import threading
import time

import requests
import urllib3

from middleware.config import Settings
from middleware.db import init_db
from middleware.minio import S3Uploader
from middleware.plugins.loader import load_vendor_plugin

urllib3.disable_warnings()

logger = logging.getLogger(__name__)

# Every field is read from the environment; mypy cannot see that without the
# pydantic plugin, which this repository does not configure.
settings = Settings()  # type: ignore[call-arg]

HTTP_TIMEOUT = float(settings.JOB_REPORTER_HTTP_TIMEOUT or settings.UPSTREAM_TIMEOUT)
# The same TLS and timeout settings as the reporter: both talk to the same machine.
VERIFY_TLS = (
    settings.JOB_REPORTER_VERIFY_TLS
    if settings.JOB_REPORTER_VERIFY_TLS is not None
    else settings.VERIFY_UPSTREAM_SSL
)

# Set by SIGTERM/SIGINT. An Event rather than a flag, so waiting on it both
# sleeps and wakes the moment `docker stop` asks — no one-second polling.
_stop = threading.Event()


def _handle_signal(signum, frame):
    logger.info("Received signal %s, shutting down calibration poller...", signum)
    _stop.set()


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _refused(error: Exception) -> int | None:
    """The status code if the machine refused our credential, else ``None``."""
    response = getattr(error, "response", None)
    if (
        isinstance(error, requests.HTTPError)
        and response is not None
        and response.status_code in (401, 403)
    ):
        return int(response.status_code)
    return None


# After a failure the next poll comes sooner than the interval: first after
# this many seconds, then doubling up to the interval itself. The failure that
# prompted it is a deploy: `docker compose down && up` starts this container
# while the reverse proxy in front of the object store is still initialising,
# so the first poll finds nothing listening on 443 — and at an hour's interval
# the first report would have waited an hour for no reason.
RETRY_AFTER = 60


def _unreachable(error: Exception) -> bool:
    """Whether a failure was a host not answering, rather than anything it said."""
    return isinstance(
        error, requests.ConnectionError | requests.Timeout | urllib3.exceptions.HTTPError
    )


def poll_once(vendor_plugin, headers: dict[str, str]) -> None:
    uploader = S3Uploader(
        minio_server_url=settings.MINIO_SERVER_URL,
        bucket_name=settings.BUCKET_NAME,
        app_user=settings.APP_USER,
        app_password=settings.APP_PASSWORD,
    )
    # The store first. Reports are several MB each and are downloaded before
    # they are uploaded, so without this an unreachable store costs a full
    # download of every pending report, and a traceback for each, per poll.
    uploader.ensure_bucket()
    vendor_plugin.process_calibration_runs(
        machine_url=settings.MACHINE_URL,
        headers=headers,
        uploader=uploader,
        db_init_fn=init_db,
        timeout=HTTP_TIMEOUT,
        verify_tls=VERIFY_TLS,
    )


def main() -> None:
    vendor_plugin = load_vendor_plugin(settings)
    interval = vendor_plugin.get_calibration_poll_interval()

    if interval <= 0:
        # A vendor with no calibration concept. Idle rather than exit, or the
        # restart policy would turn "nothing to do" into a crash loop.
        logger.info("Calibration polling disabled by the vendor plugin; idling.")
        _stop.wait()
        return

    headers = vendor_plugin.build_calibration_headers()
    logger.info("Calibration poller started, every %ds", interval)

    refused_with: int | None = None
    unreachable = False
    retry = RETRY_AFTER
    while not _stop.is_set():
        started = time.monotonic()
        wait = interval
        try:
            poll_once(vendor_plugin, headers)
            if refused_with is not None:
                logger.info("Calibration: the machine accepts our credential again")
            if unreachable:
                logger.info("Calibration: the machine and the object store are reachable again")
            refused_with, unreachable, retry = None, False, RETRY_AFTER
        except Exception as e:
            code = _refused(e)
            if code is None:
                # Anything but a refused credential may clear up by itself.
                wait, retry = min(retry, interval), min(retry * 2, interval)
            if code is None and _unreachable(e):
                # One line, not the client library's retry chain as a traceback.
                (logger.debug if unreachable else logger.warning)(
                    "Calibration: cannot reach the machine or the object store (%s); "
                    "retrying in %ds",
                    e,
                    wait,
                )
                unreachable = True
            elif code is None:
                logger.exception("Calibration: poll failed, retrying in %ds: %s", wait, e)
            elif code != refused_with:
                # Said once when it starts, not every hour: this is a standing
                # configuration problem, and repeating it trains people to
                # ignore the log. Recovery is announced above.
                logger.warning(
                    "Calibration: the machine refuses our credential (%s) on its calibration "
                    "endpoints. Set ADMIN_IQM_TOKEN to an administrator token; polling "
                    "continues and will recover by itself.",
                    code,
                )
                refused_with = code
            else:
                logger.debug("Calibration: still refused (%s)", code)
        _stop.wait(max(0.0, wait - (time.monotonic() - started)))

    logger.info("Calibration poller stopped.")


if __name__ == "__main__":
    main()
