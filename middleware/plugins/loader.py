"""Plugin discovery and loading."""

from __future__ import annotations

import importlib
import logging
from typing import Any

from middleware.plugins.interfaces import (
    AuthPlugin,
    PolicyPlugin,
    SitePlugin,
    VendorPlugin,
)

logger = logging.getLogger(__name__)

_VENDOR_REGISTRY: dict[str, str] = {
    "iqm": "middleware.vendors.iqm.plugin.IQMVendorPlugin",
}

_SITE_REGISTRY: dict[str, str] = {
    "spark": "middleware.sites.spark.plugin.SparkSitePlugin",
}

_AUTH_REGISTRY: dict[str, str] = {
    "keycloak": "middleware.auth.keycloak.KeycloakAuthPlugin",
    "sqed": "middleware.auth.sqed.SqedAuthPlugin",
}

_POLICY_REGISTRY: dict[str, str] = {
    "passthrough": "middleware.policy.passthrough.PassthroughPolicyPlugin",
    "shot_budget": "middleware.policy.shot_budget.ShotBudgetPolicyPlugin",
}


def _load_class(registry: dict[str, str], name: str) -> type:
    """Resolve a plugin name to its class via the registry."""
    if name not in registry:
        available = ", ".join(sorted(registry.keys()))
        raise ValueError(f"Unknown plugin {name!r}. Available: {available}")
    cls_path = registry[name]
    module_path, cls_name = cls_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)


def load_vendor_plugin(settings: Any) -> VendorPlugin:
    """Load and instantiate the configured vendor plugin."""
    name = getattr(settings, "VENDOR_PLUGIN", "iqm")
    logger.info("Loading vendor plugin: %s", name)
    cls = _load_class(_VENDOR_REGISTRY, name)
    return cls(settings)


def load_site_plugin(settings: Any) -> SitePlugin:
    """Load and instantiate the configured site plugin."""
    name = getattr(settings, "SITE_PLUGIN", "spark")
    logger.info("Loading site plugin: %s", name)
    cls = _load_class(_SITE_REGISTRY, name)
    return cls(settings)


def load_auth_plugins(settings: Any) -> list[AuthPlugin]:
    """Load the configured auth plugin chain, in configuration order.

    Order is significant for prefix-less plugins (see
    ``middleware.authentication.authenticate``), so the configured order is
    preserved rather than normalised.
    """
    names = [
        n.strip() for n in getattr(settings, "AUTH_PLUGINS", "keycloak").split(",") if n.strip()
    ]
    if not names:
        raise ValueError("AUTH_PLUGINS is empty: the gateway would accept no credential at all")
    logger.info("Loading auth plugins: %s", names)
    return [_load_class(_AUTH_REGISTRY, name)(settings) for name in names]


def load_policy_plugin(settings: Any, limiter: Any) -> PolicyPlugin:
    """Load and instantiate the configured policy plugin.

    The concurrency limiter is passed in rather than constructed here: both
    shipped plugins reserve against the same Redis counters, and two limiter
    instances would mean two views of one budget.
    """
    name = getattr(settings, "POLICY_PLUGIN", "passthrough")
    logger.info("Loading policy plugin: %s", name)
    cls = _load_class(_POLICY_REGISTRY, name)
    return cls(settings, limiter)
