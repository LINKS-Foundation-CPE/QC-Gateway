"""Auth plugin chain routing (middleware.authentication.authenticate)."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from middleware.authentication import authenticate
from middleware.plugins.datatypes import Principal
from middleware.plugins.interfaces import AuthError


def _request(auth_header: str | None) -> SimpleNamespace:
    headers = {} if auth_header is None else {"Authorization": auth_header}
    return SimpleNamespace(headers=headers)


def _principal(source: str) -> Principal:
    return Principal(auth_source=source, uid="u1", username="alice", roles=["cortex_user"])


class StubPlugin:
    def __init__(self, name, prefixes, result=None, error=None):
        self.name = name
        self.prefixes = prefixes
        self._result = result
        self._error = error
        self.calls: list[str] = []

    async def validate(self, token):
        self.calls.append(token)
        if self._error is not None:
            raise self._error
        return self._result


@pytest.mark.asyncio
async def test_missing_header_is_401():
    with pytest.raises(HTTPException) as exc:
        await authenticate(_request(None), [], strict_prefix_mode=False)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_non_bearer_header_is_401():
    with pytest.raises(HTTPException) as exc:
        await authenticate(_request("Basic abc"), [], strict_prefix_mode=False)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_prefix_claim_routes_to_owning_plugin_only():
    fallback = StubPlugin("keycloak", [], result=_principal("keycloak"))
    sqed = StubPlugin("sqed", ["sqed_"], result=_principal("sqed"))
    principal = await authenticate(
        _request("Bearer sqed_abc"), [fallback, sqed], strict_prefix_mode=False
    )
    assert principal.auth_source == "sqed"
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_prefix_claim_none_is_hard_401_not_fallthrough():
    sqed = StubPlugin("sqed", ["sqed_"], result=None)
    fallback = StubPlugin("keycloak", [], result=_principal("keycloak"))
    with pytest.raises(HTTPException) as exc:
        await authenticate(_request("Bearer sqed_abc"), [sqed, fallback], strict_prefix_mode=False)
    assert exc.value.status_code == 401
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_autherror_carries_status_and_detail():
    sqed = StubPlugin("sqed", ["sqed_"], error=AuthError("token backend unavailable", 503))
    with pytest.raises(HTTPException) as exc:
        await authenticate(_request("Bearer sqed_abc"), [sqed], strict_prefix_mode=False)
    assert exc.value.status_code == 503
    assert exc.value.detail == "token backend unavailable"


@pytest.mark.asyncio
async def test_fallback_chain_first_principal_wins():
    declines = StubPlugin("first", [], result=None)
    accepts = StubPlugin("second", [], result=_principal("second"))
    principal = await authenticate(
        _request("Bearer eyJ.x.y"), [declines, accepts], strict_prefix_mode=False
    )
    assert principal.auth_source == "second"
    assert declines.calls == ["eyJ.x.y"]


@pytest.mark.asyncio
async def test_fallback_skips_prefixed_plugins():
    sqed = StubPlugin("sqed", ["sqed_"], result=_principal("sqed"))
    keycloak = StubPlugin("keycloak", [], result=_principal("keycloak"))
    principal = await authenticate(
        _request("Bearer eyJ.x.y"), [sqed, keycloak], strict_prefix_mode=False
    )
    assert principal.auth_source == "keycloak"
    assert sqed.calls == []


@pytest.mark.asyncio
async def test_all_decline_is_401():
    a = StubPlugin("a", [], result=None)
    b = StubPlugin("b", [], result=None)
    with pytest.raises(HTTPException) as exc:
        await authenticate(_request("Bearer zzz"), [a, b], strict_prefix_mode=False)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_strict_prefix_mode_rejects_unprefixed_tokens():
    keycloak = StubPlugin("keycloak", [], result=_principal("keycloak"))
    with pytest.raises(HTTPException) as exc:
        await authenticate(_request("Bearer eyJ.x.y"), [keycloak], strict_prefix_mode=True)
    assert exc.value.status_code == 401
    assert keycloak.calls == []
