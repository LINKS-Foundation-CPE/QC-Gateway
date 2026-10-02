"""Sqed auth plugin configuration.

Plugin-local by convention: vendor and site settings live next to the plugin
that reads them, not in ``middleware/config.py``, so a deployment that does
not load this plugin never sees these variables.
"""

from pydantic_settings import BaseSettings


class SqedSettings(BaseSettings):
    # Token prefix this plugin claims. A prefix is a hard routing claim, so it
    # must not collide with any other plugin's.
    SQED_TOKEN_PREFIX: str = "sqed_"

    # Roles granted to every principal this plugin authenticates,
    # comma-separated. The scheduler vouches for the account; it says nothing
    # about privileges, so the baseline is the ordinary submit role and
    # anything beyond it comes from the lookup below.
    SQED_DEFAULT_ROLES: str = "cortex_user"

    # Optional per-user roles lookup against the portal backend, e.g.
    # http://quantum-api:8500/userRoles — queried as {url}/{username} and
    # expected to return {"roles": [...]}. Looked-up roles are ADDED to the
    # defaults; an empty value disables the lookup entirely.
    SQED_ROLES_LOOKUP_URL: str = ""

    # Cache TTL, in seconds, for a successful or empty lookup result.
    SQED_ROLES_CACHE_TTL: int = 60

    # Verify TLS on the roles lookup. Only relevant for an https:// lookup
    # URL; set false only for a deployment whose backend presents a private
    # certificate the container does not trust.
    SQED_ROLES_VERIFY_TLS: bool = True
