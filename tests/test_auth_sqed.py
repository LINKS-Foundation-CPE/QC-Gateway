"""SqedAuthPlugin against the qpu:token hash contract (fakeredis)."""

import json
import time
from types import SimpleNamespace

import fakeredis
import pytest

from middleware.plugins.interfaces import AuthError

NOW = int(time.time())


def _record(
    uid=1000,
    username="alice",
    n_licenses=1,
    issued=NOW - 60,
    expires=NOW + 3600,
    account=None,
):
    rec = {
        "uid": uid,
        "n_licenses": n_licenses,
        "issued_at": issued,
        "expires_at": expires,
    }
    if username is not None:
        rec["username"] = username
    if account is not None:
        rec["account"] = account
    return json.dumps(rec)


@pytest.fixture()
def plugin(monkeypatch):
    server = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    from middleware import job_counters

    monkeypatch.setattr(job_counters, "get_redis_client", lambda: client)
    from middleware.auth.sqed import SqedAuthPlugin

    p = SqedAuthPlugin(SimpleNamespace())
    p._redis = client
    return p


@pytest.mark.asyncio
async def test_prefix_is_declared(plugin):
    assert plugin.prefixes == ["sqed_"]


@pytest.mark.asyncio
async def test_single_job_token(plugin):
    plugin._redis.hset("qpu:token:sqed_aa", "101", _record(n_licenses=2))
    principal = await plugin.validate("sqed_aa")
    assert principal.auth_source == "sqed"
    assert principal.username == "alice"
    assert principal.uid == "1000"
    assert principal.roles == ["cortex_user"]
    assert principal.metadata["job_ids"] == ["101"]
    assert principal.metadata["n_licenses"] == 2


@pytest.mark.asyncio
async def test_array_token_sums_licenses_over_live_fields(plugin):
    for job_id in ("201", "202", "203"):
        plugin._redis.hset("qpu:token:sqed_bb", job_id, _record(n_licenses=1))
    principal = await plugin.validate("sqed_bb")
    assert principal.metadata["job_ids"] == ["201", "202", "203"]
    assert principal.metadata["n_licenses"] == 3


@pytest.mark.asyncio
async def test_missing_token_is_401(plugin):
    with pytest.raises(AuthError) as exc:
        await plugin.validate("sqed_nope")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_drifted_expiry_is_filtered(plugin):
    plugin._redis.hset("qpu:token:sqed_cc", "301", _record(expires=NOW - 10))
    plugin._redis.hset("qpu:token:sqed_cc", "302", _record(n_licenses=4))
    principal = await plugin.validate("sqed_cc")
    assert principal.metadata["job_ids"] == ["302"]
    assert principal.metadata["n_licenses"] == 4


@pytest.mark.asyncio
async def test_all_fields_drifted_expired_is_401(plugin):
    plugin._redis.hset("qpu:token:sqed_dd", "401", _record(expires=NOW - 10))
    with pytest.raises(AuthError):
        await plugin.validate("sqed_dd")


@pytest.mark.asyncio
async def test_username_falls_back_to_uid(plugin):
    plugin._redis.hset("qpu:token:sqed_ee", "501", _record(username=None))
    principal = await plugin.validate("sqed_ee")
    assert principal.username == "1000"


@pytest.mark.asyncio
async def test_malformed_record_is_skipped_not_fatal(plugin):
    plugin._redis.hset("qpu:token:sqed_ff", "601", "not-json")
    plugin._redis.hset("qpu:token:sqed_ff", "602", _record())
    principal = await plugin.validate("sqed_ff")
    assert principal.metadata["job_ids"] == ["602"]


@pytest.mark.asyncio
async def test_redis_down_is_503(monkeypatch):
    from middleware import job_counters

    monkeypatch.setattr(job_counters, "get_redis_client", lambda: None)
    from middleware.auth.sqed import SqedAuthPlugin

    p = SqedAuthPlugin(SimpleNamespace())
    with pytest.raises(AuthError) as exc:
        await p.validate("sqed_gg")
    assert exc.value.status_code == 503


class _StubResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class _StubHttp:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def get(self, url):
        self.calls.append(url)
        return self._response


@pytest.mark.asyncio
async def test_roles_lookup_adds_pulla_user(plugin):
    plugin._settings.SQED_ROLES_LOOKUP_URL = "http://api.test/userRoles"
    plugin._http = _StubHttp(_StubResponse(200, {"roles": ["cortex_user", "pulla_user"]}))
    plugin._redis.hset("qpu:token:sqed_rl1", "701", _record())
    principal = await plugin.validate("sqed_rl1")
    assert principal.roles == ["cortex_user", "pulla_user"]
    assert plugin._http.calls == ["http://api.test/userRoles/alice"]


@pytest.mark.asyncio
async def test_roles_lookup_404_keeps_defaults(plugin):
    plugin._settings.SQED_ROLES_LOOKUP_URL = "http://api.test/userRoles"
    plugin._http = _StubHttp(_StubResponse(404))
    plugin._redis.hset("qpu:token:sqed_rl2", "702", _record())
    principal = await plugin.validate("sqed_rl2")
    assert principal.roles == ["cortex_user"]


@pytest.mark.asyncio
async def test_roles_lookup_is_cached(plugin):
    plugin._settings.SQED_ROLES_LOOKUP_URL = "http://api.test/userRoles"
    plugin._http = _StubHttp(_StubResponse(200, {"roles": ["pulla_user"]}))
    plugin._redis.hset("qpu:token:sqed_rl3", "703", _record())
    await plugin.validate("sqed_rl3")
    await plugin.validate("sqed_rl3")
    assert len(plugin._http.calls) == 1


@pytest.mark.asyncio
async def test_roles_lookup_disabled_when_unset(plugin):
    plugin._http = _StubHttp(_StubResponse(200, {"roles": ["pulla_user"]}))
    plugin._redis.hset("qpu:token:sqed_rl4", "704", _record())
    principal = await plugin.validate("sqed_rl4")
    assert principal.roles == ["cortex_user"]
    assert plugin._http.calls == []


@pytest.mark.asyncio
async def test_account_is_exposed_as_metadata(plugin):
    """The SLURM --account travels to the core, which reports it as the project."""
    plugin._redis.hset("qpu:token:sqed_acct", "301", _record(account="cin_proj_a"))
    principal = await plugin.validate("sqed_acct")
    assert principal.metadata["account"] == "cin_proj_a"


@pytest.mark.asyncio
async def test_account_absent_is_none_not_missing(plugin):
    """A record without an account must not make the key disappear: the core
    reads it unconditionally and falls back to the client-declared project."""
    plugin._redis.hset("qpu:token:sqed_noacct", "302", _record())
    principal = await plugin.validate("sqed_noacct")
    assert principal.metadata["account"] is None
