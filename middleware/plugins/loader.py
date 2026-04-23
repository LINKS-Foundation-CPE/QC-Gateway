"""Plugin discovery and loading."""

from __future__ import annotations

import importlib
import logging
from typing import Any

from middleware.plugins.interfaces import SitePlugin, VendorPlugin

logger = logging.getLogger(__name__)

_VENDOR_REGISTRY: dict[str, str] = {
    "iqm": "middleware.vendors.iqm.plugin.IQMVendorPlugin",
}

_SITE_REGISTRY: dict[str, str] = {
    "spark": "middleware.sites.spark.plugin.SparkSitePlugin",
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
