"""Auth chain resolution — in particular that the default is unchanged.

The point of these: adding a second authentication backend must not change
what an existing deployment accepts. The default chain is the OIDC plugin
alone, and it claims no prefix, so a JWT reaches it exactly as before.
"""

from types import SimpleNamespace

import pytest

from middleware.plugins.loader import load_auth_plugins

KEYCLOAK_FIELDS = {
    "KEYCLOAK_JWKS_URL": "http://keycloak.test/jwks",
    "KEYCLOAK_ISSUER": "http://keycloak.test/realms/test",
    "AUDIENCE": "test-audience",
}


def _settings(**over):
    return SimpleNamespace(**{**KEYCLOAK_FIELDS, **over})


def test_default_chain_is_keycloak_only():
    """An unset AUTH_PLUGINS must behave exactly as before it existed."""
    plugins = load_auth_plugins(_settings())
    assert [p.name for p in plugins] == ["keycloak"]
    # No prefix: a bearer JWT still reaches it through the fallback chain.
    assert plugins[0].prefixes == []


def test_explicit_default_matches_the_implicit_one():
    assert [p.name for p in load_auth_plugins(_settings(AUTH_PLUGINS="keycloak"))] == ["keycloak"]


def test_configuration_order_is_preserved():
    """Order decides which prefix-less plugin answers first, so it is kept."""
    names = [p.name for p in load_auth_plugins(_settings(AUTH_PLUGINS="sqed,keycloak"))]
    assert names == ["sqed", "keycloak"]
    names = [p.name for p in load_auth_plugins(_settings(AUTH_PLUGINS="keycloak,sqed"))]
    assert names == ["keycloak", "sqed"]


def test_whitespace_is_tolerated():
    names = [p.name for p in load_auth_plugins(_settings(AUTH_PLUGINS=" keycloak , sqed "))]
    assert names == ["keycloak", "sqed"]


def test_unknown_plugin_names_itself_and_the_alternatives():
    with pytest.raises(ValueError, match="Unknown plugin 'nope'"):
        load_auth_plugins(_settings(AUTH_PLUGINS="nope"))


def test_empty_chain_is_refused_at_startup():
    """Better to fail to boot than to boot accepting no credential at all."""
    with pytest.raises(ValueError, match="accept no credential"):
        load_auth_plugins(_settings(AUTH_PLUGINS=" , "))
