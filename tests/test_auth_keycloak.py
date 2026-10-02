"""KeycloakAuthPlugin token-shape handling (no network)."""

from types import SimpleNamespace

import pytest

from middleware.auth.keycloak import KeycloakAuthPlugin


@pytest.fixture()
def plugin():
    return KeycloakAuthPlugin(
        SimpleNamespace(
            KEYCLOAK_JWKS_URL="http://keycloak.test/jwks",
            KEYCLOAK_ISSUER="http://keycloak.test/realms/test",
            AUDIENCE="test-audience",
        )
    )


@pytest.mark.asyncio
async def test_declares_no_prefix(plugin):
    assert plugin.prefixes == []


@pytest.mark.asyncio
async def test_non_jwt_shaped_token_declines(plugin):
    assert await plugin.validate("sqed_abcdef") is None
    assert await plugin.validate("plain-token") is None
    assert await plugin.validate("a.b.c.d") is None
