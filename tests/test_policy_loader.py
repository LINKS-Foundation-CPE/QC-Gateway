"""Policy plugin resolution — in particular that the default is unchanged.

The default is ``passthrough``, which delegates straight to the concurrency
limiter, so turning this plugin point on changes nothing for a deployment
that configures nothing.
"""

from types import SimpleNamespace

import pytest

from middleware.plugins.loader import load_policy_plugin


class _Limiter:
    """Stand-in: the loader must pass the limiter through, not build one."""


def _settings(**over):
    return SimpleNamespace(
        MAX_CONCURRENT_SHOTS=1_000_000,
        MAX_CONCURRENT_SWEEPS=4,
        **over,
    )


def test_default_plugin_is_passthrough():
    limiter = _Limiter()
    plugin = load_policy_plugin(_settings(), limiter)
    assert plugin.name == "passthrough"
    # The shared limiter is handed through: two instances would mean two
    # views of one Redis budget.
    assert plugin._limiter is limiter


def test_explicit_default_matches_the_implicit_one():
    assert load_policy_plugin(_settings(POLICY_PLUGIN="passthrough"), _Limiter()).name == (
        "passthrough"
    )


def test_shot_budget_is_selectable():
    plugin = load_policy_plugin(_settings(POLICY_PLUGIN="shot_budget"), _Limiter())
    assert plugin.name == "shot_budget"


def test_unknown_plugin_names_itself_and_the_alternatives():
    with pytest.raises(ValueError, match="Unknown plugin 'nope'"):
        load_policy_plugin(_settings(POLICY_PLUGIN="nope"), _Limiter())
