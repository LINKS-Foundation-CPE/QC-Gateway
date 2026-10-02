"""OIDC (Keycloak) JWT authentication plugin.

The historical behaviour of this gateway, unchanged: RS256 bearer tokens
validated against the realm's JWKS endpoint, with roles read from
``realm_access.roles``.

Claims no token prefix. A JWT does not announce its issuer in its first
bytes, so this plugin sits in the fallback chain and declines
(returns ``None``) anything that is not JWT-shaped, leaving other formats to
plugins that own a prefix.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import jwt
from jwt import InvalidTokenError, PyJWKClient, PyJWKClientError

from middleware.plugins.datatypes import Principal
from middleware.plugins.interfaces import AuthError

logger = logging.getLogger(__name__)


class KeycloakAuthPlugin:
    """Validates OIDC-issued JWTs and maps them to a ``Principal``."""

    name = "keycloak"
    prefixes: ClassVar[list[str]] = []

    def __init__(self, settings: Any) -> None:
        self._jwks_client = PyJWKClient(settings.KEYCLOAK_JWKS_URL, cache_keys=True)
        self._issuer = settings.KEYCLOAK_ISSUER
        self._audience = settings.AUDIENCE

    async def validate(self, token: str) -> Principal | None:
        # Three dot-separated segments: cheap enough to do before touching the
        # JWKS cache, and it is what makes "not a JWT" distinguishable from
        # "a JWT that failed", which is the difference between falling through
        # to the next plugin and returning 401.
        if token.count(".") != 2:
            return None
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token).key
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.exceptions.DecodeError:
            # Shaped like a JWT but is not one — another plugin's token.
            return None
        except (InvalidTokenError, PyJWKClientError) as e:
            # A real JWT that did not verify: expired, wrong audience or
            # issuer, unknown signing key. Definitively refused.
            raise AuthError(f"Invalid token: {e!s}") from e

        return Principal(
            auth_source=self.name,
            uid=payload["sub"],
            username=payload.get("preferred_username", ""),
            roles=payload.get("realm_access", {}).get("roles", []),
            metadata={"claims": payload},
        )
