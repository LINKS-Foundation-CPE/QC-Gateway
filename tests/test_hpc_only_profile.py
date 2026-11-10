"""The HPC-only deployment profile, asserted rather than assumed.

In this profile quantum access arrives only through SLURM (a portal such as
OpenOnDemand submits a batch job like any other), with no direct web-API
submission. It is a set of configuration values, not a different build:
`AUTH_PLUGINS=sqed`, `STRICT_PREFIX_MODE=true`, `SITE_PLUGIN=sqed`.

What these tests pin down is the part worth being sure of: with the profile in
force the cloud submission path is *closed*, not merely unused. A Keycloak JWT
cannot authenticate a submission, because no plugin claims prefix-less tokens
and STRICT_PREFIX_MODE refuses to fall back.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from middleware.authentication import authenticate
from middleware.plugins.datatypes import Principal
from middleware.plugins.loader import load_auth_plugins


def _request(token: str) -> SimpleNamespace:
    return SimpleNamespace(headers={"Authorization": f"Bearer {token}"})


class _SqedStub:
    """Stands in for SqedAuthPlugin: claims the sqed_ prefix, nothing else."""

    name = "sqed"
    prefixes = ("sqed_",)

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def validate(self, token: str) -> Principal:
        self.calls.append(token)
        return Principal(auth_source="sqed", uid="1000", username="alice", roles=["cortex_user"])


# A prefix-less token is what any OIDC-issued JWT looks like to the router.
JWT_LIKE = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"


@pytest.mark.asyncio
async def test_profile_rejects_a_jwt_submission():
    sqed = _SqedStub()
    with pytest.raises(HTTPException) as exc:
        await authenticate(_request(JWT_LIKE), [sqed], strict_prefix_mode=True)
    assert exc.value.status_code == 401
    # Not merely rejected: never even offered to the cluster-token plugin.
    assert sqed.calls == []


@pytest.mark.asyncio
async def test_profile_still_accepts_a_cluster_token():
    sqed = _SqedStub()
    principal = await authenticate(_request("sqed_" + "a" * 32), [sqed], strict_prefix_mode=True)
    assert principal.auth_source == "sqed"
    assert principal.username == "alice"


@pytest.mark.asyncio
async def test_without_strict_mode_a_jwt_would_reach_a_fallback_plugin():
    """The inverse, so the guarantee above is attributable to the profile.

    Without STRICT_PREFIX_MODE a prefix-less plugin in the chain gets the
    token — which is exactly the cloud path, and exactly what the profile
    turns off. This is the reversal point: adding keycloak back to
    AUTH_PLUGINS and clearing the flag restores it.
    """

    class _KeycloakStub:
        name = "keycloak"
        prefixes = ()

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def validate(self, token: str) -> Principal:
            self.calls.append(token)
            return Principal(
                auth_source="keycloak", uid="u1", username="bob", roles=["cortex_user"]
            )

    keycloak = _KeycloakStub()
    principal = await authenticate(
        _request(JWT_LIKE), [_SqedStub(), keycloak], strict_prefix_mode=False
    )
    assert principal.auth_source == "keycloak"
    assert keycloak.calls == [JWT_LIKE]


def test_profile_loads_the_cluster_plugin_alone():
    plugins = load_auth_plugins(SimpleNamespace(AUTH_PLUGINS="sqed"))
    assert [p.name for p in plugins] == ["sqed"]


def test_default_configuration_is_unchanged_for_lagrange():
    """Nothing about the profile leaks into a deployment that does not set it."""
    plugins = load_auth_plugins(
        SimpleNamespace(
            KEYCLOAK_JWKS_URL="http://keycloak.test/jwks",
            KEYCLOAK_ISSUER="http://keycloak.test/realms/test",
            AUDIENCE="account",
        )
    )
    assert [p.name for p in plugins] == ["keycloak"]
