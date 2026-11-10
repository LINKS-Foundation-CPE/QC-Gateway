"""SqedSitePlugin — SLURM-authorized sqed path, SPARK delegation otherwise."""

from types import SimpleNamespace

from middleware.plugins.datatypes import JobAuthorizationResult, Principal
from middleware.plugins.loader import _SITE_REGISTRY, _load_class
from middleware.sites.sqed.plugin import SqedSitePlugin

SETTINGS = SimpleNamespace(UPSTREAM_TIMEOUT=30)


def _sqed_principal():
    return Principal(
        auth_source="sqed",
        uid="1000",
        username="hpcuser01",
        roles=["cortex_user"],
        metadata={"job_ids": ["42"], "n_licenses": 1},
    )


def _cloud_principal():
    return Principal(auth_source="keycloak", uid="u1", username="alice@example.com")


class SparkStub:
    def __init__(self):
        self.calls = []
        self.job_types = []

    async def authorize_job(self, principal, project_name, extra_headers, timeout, job_type=None):
        self.calls.append(principal.username)
        self.job_types.append(job_type)
        return JobAuthorizationResult(is_authorized=False, status_code=403)


def _plugin():
    plugin = SqedSitePlugin(SETTINGS)
    plugin._spark = SparkStub()
    return plugin


async def test_sqed_principal_skips_site_authorization():
    plugin = _plugin()
    result = await plugin.authorize_job(_sqed_principal(), "proj", None, 5.0)
    assert result.is_authorized
    assert plugin._spark.calls == []  # portal never consulted


async def test_cloud_principal_delegates_to_spark():
    plugin = _plugin()
    result = await plugin.authorize_job(_cloud_principal(), "proj", None, 5.0)
    assert not result.is_authorized
    assert plugin._spark.calls == ["alice@example.com"]


async def test_job_type_reaches_the_portal_check():
    """A delegated principal must still be gated on what it submits.

    Dropping ``job_type`` here would silently un-gate the privileged sweep
    path for anyone authorized through the portal on the sqed site.
    """
    plugin = _plugin()
    await plugin.authorize_job(_cloud_principal(), "proj", None, 5.0, job_type="sweep")
    assert plugin._spark.job_types == ["sweep"]


def test_the_loader_knows_the_sqed_site_plugin():
    """SITE_PLUGIN=sqed must resolve: a registry entry lost in a merge once made
    every --with-slurm gateway fail at startup with "Unknown plugin 'sqed'"."""
    assert _load_class(_SITE_REGISTRY, "sqed") is SqedSitePlugin
