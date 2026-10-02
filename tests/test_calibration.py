"""Calibration polling: run dating, planning, the day-by-day index, credentials.

The shapes here are the machine's own: a run carries naive ISO `start_time` and
`end_time`, which are UTC — a report's `Last-Modified` (always GMT) equals its
run's `end_time` to the second.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests

from middleware.vendors.iqm.calibration import (
    build_manifest,
    plan_runs,
    render_index,
    report_object_name,
    run_timestamp,
)

ROME = ZoneInfo("Europe/Rome")


def _run(status="ready", success=True, cal="c6fb8c58-af46-41b9-b66a-8685200e9d99", **times):
    return {"status": status, "result": {"success": success, "calibration_set_id": cal}, **times}


# ── dating a run ─────────────────────────────────────────────────────────────


def test_a_run_is_dated_by_when_it_finished():
    ts, key = run_timestamp(
        _run(start_time="2026-08-17T03:37:25.094994", end_time="2026-08-17T03:37:48.331461")
    )
    assert key == "end_time"
    assert ts == datetime(2026, 8, 17, 3, 37, 48, 331461, tzinfo=UTC)


def test_a_naive_machine_timestamp_is_utc_not_local():
    ts, _ = run_timestamp(_run(end_time="2026-08-17T03:37:48"))
    assert ts.utcoffset() == timedelta(0)


def test_start_time_is_the_fallback():
    ts, key = run_timestamp(_run(start_time="2026-05-20T02:27:22"))
    assert key == "start_time"
    assert ts.hour == 2


@pytest.mark.parametrize("info", [_run(), _run(end_time=""), _run(end_time="not a date")])
def test_no_usable_time_says_so(info):
    assert run_timestamp(info) == (None, None)


# ── what a poll has to do ────────────────────────────────────────────────────


def test_only_finished_successful_runs_with_a_set_are_fetched():
    runs = {
        "ok": _run(),
        "running": _run(status="running"),
        "failed": _run(success=False),
        "no-set": _run(cal=None),
    }
    plan = plan_runs(runs, processed={})
    assert [r[0] for r in plan.to_fetch] == ["ok"]
    assert plan.to_backfill == []


def test_a_run_already_fetched_is_not_fetched_again():
    plan = plan_runs({"done": _run()}, processed={"done": True})
    assert plan.to_fetch == [] and plan.to_backfill == []


def test_a_run_fetched_before_dates_were_recorded_is_backfilled():
    """The upgrade path: rows written by the old poller have no date."""
    plan = plan_runs({"old": _run(end_time="2026-05-20T02:27:38")}, processed={"old": False})
    assert plan.to_fetch == []
    assert [r[0] for r in plan.to_backfill] == ["old"]


# ── the index ────────────────────────────────────────────────────────────────


def _row(run, cal, ts=None, processed=None, size=4467480):
    return (run, cal, ts, processed or datetime(2026, 9, 1, tzinfo=UTC), size)


def test_days_are_cut_in_the_deployment_zone():
    """22:30 UTC on the 16th is 00:30 on the 17th in Turin, and belongs there."""
    m = build_manifest(
        [_row("r", "c", ts=datetime(2026, 8, 16, 22, 30, tzinfo=UTC))],
        "https://store/bucket",
        ROME,
        "Europe/Rome",
    )
    (r,) = m["reports"]
    assert (r["date"], r["time"]) == ("2026-08-17", "00:30")
    assert m["timezone"] == "Europe/Rome"


def test_links_point_where_job_pages_already_point():
    """Jobs link reports by calibration set id before knowing they exist."""
    m = build_manifest(
        [_row("r", "abc", ts=datetime(2026, 8, 17, tzinfo=UTC))], "https://s/b", UTC, "UTC"
    )
    assert m["reports"][0]["url"] == f"https://s/b/{report_object_name('abc')}"
    assert report_object_name("abc") == "calibration/abc_report.zip"


def test_an_undated_report_is_kept_and_marked_approximate():
    m = build_manifest(
        [_row("r", "c", ts=None, processed=datetime(2026, 3, 2, 9, 15, tzinfo=UTC))],
        "https://s/b",
        UTC,
        "UTC",
    )
    (r,) = m["reports"]
    assert r["approximate"] is True
    assert r["date"] == "2026-03-02"


def test_newest_first():
    m = build_manifest(
        [
            _row("old", "a", ts=datetime(2026, 5, 20, tzinfo=UTC)),
            _row("new", "b", ts=datetime(2026, 8, 17, tzinfo=UTC)),
        ],
        "https://s/b",
        UTC,
        "UTC",
    )
    assert [r["run_id"] for r in m["reports"]] == ["new", "old"]


def test_the_page_groups_by_day_and_escapes_what_it_prints():
    m = build_manifest(
        [
            _row("r1", "<b>x</b>", ts=datetime(2026, 8, 17, 3, tzinfo=UTC)),
            _row("r2", "y", ts=datetime(2026, 8, 17, 9, tzinfo=UTC)),
            _row("r3", "z", ts=datetime(2026, 5, 20, 2, tzinfo=UTC)),
        ],
        "https://s/b",
        UTC,
        "UTC",
    )
    page = render_index(m)
    assert page.count('class="day"') == 2
    assert "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page
    assert page.index("Monday 17 August 2026") < page.index("Wednesday 20 May 2026")


def test_an_empty_index_says_so():
    page = render_index(build_manifest([], "https://s/b", UTC, "UTC"))
    assert "No calibration reports yet" in page


# ── credentials ──────────────────────────────────────────────────────────────


def _plugin(monkeypatch, **env):
    for key in ("IQM_SERVER_TOKEN", "ADMIN_IQM_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from middleware.vendors.iqm.plugin import IQMVendorPlugin

    return IQMVendorPlugin(SimpleNamespace(MINIO_SERVER_URL="https://s", BUCKET_NAME="b"))


def test_calibration_uses_the_admin_token_when_there_is_one(monkeypatch):
    p = _plugin(monkeypatch, IQM_SERVER_TOKEN="device", ADMIN_IQM_TOKEN="admin")
    assert p.build_calibration_headers() == {"Authorization": "Bearer admin"}


def test_the_admin_token_never_reaches_the_job_path(monkeypatch):
    p = _plugin(monkeypatch, IQM_SERVER_TOKEN="device", ADMIN_IQM_TOKEN="admin")
    assert p.build_upstream_headers({}) == {"Authorization": "Bearer device"}


def test_without_an_admin_token_calibration_behaves_as_before(monkeypatch):
    p = _plugin(monkeypatch, IQM_SERVER_TOKEN="device")
    assert p.build_calibration_headers() == {"Authorization": "Bearer device"}


@pytest.mark.parametrize(("code", "expected"), [(401, 401), (403, 403), (500, None)])
def test_the_poller_recognises_a_refused_credential(code, expected):
    from middleware.calibration_poller import _refused

    response = requests.Response()
    response.status_code = code
    assert _refused(requests.HTTPError(response=response)) == expected
    assert _refused(ValueError("not http")) is None


def test_an_unanswering_host_is_told_apart_from_an_answer():
    """Decides between a one-line warning with a quick retry and a traceback.

    The store's client (minio, over urllib3) and the machine's (requests) fail
    differently when nothing is listening; both must count. A refused
    credential is an answer, not an outage.
    """
    import urllib3

    from middleware.calibration_poller import _unreachable

    refused = requests.Response()
    refused.status_code = 403
    assert _unreachable(requests.ConnectionError("refused"))
    assert _unreachable(requests.Timeout("slow"))
    assert _unreachable(urllib3.exceptions.MaxRetryError(None, "/job-data", "refused"))
    assert not _unreachable(requests.HTTPError(response=refused))
    assert not _unreachable(ValueError("a bug"))
