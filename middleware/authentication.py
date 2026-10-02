"""Bearer-token authentication via pluggable auth backends.

``authenticate`` extracts the ``Authorization: Bearer`` token and routes it
through the configured ``AuthPlugin`` chain (see
``middleware/plugins/interfaces.py``):

1. If the token matches a plugin's declared prefix, that plugin alone handles
   it. A prefix is a hard claim: a token that announces its issuer and then
   fails to verify is a failure, not a reason to keep guessing.
2. Otherwise the prefix-less plugins are tried in configuration order and the
   first ``Principal`` wins. ``STRICT_PREFIX_MODE`` rejects prefix-less
   tokens outright, for a deployment that accepts scheduler tokens only.

The default chain is ``keycloak`` alone, which is the behaviour this module
had before it was pluggable. Protocol-specific validation lives in the
plugins under ``middleware/auth/`` — nothing here knows what a JWT is.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from middleware.plugins.datatypes import Principal
from middleware.plugins.interfaces import AuthError, AuthPlugin

logger = logging.getLogger(__name__)


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    return auth.removeprefix("Bearer ")


async def authenticate(
    request: Request,
    plugins: list[AuthPlugin],
    strict_prefix_mode: bool = False,
) -> Principal:
    """Authenticate the request against the configured auth plugins.

    Raises ``HTTPException`` on any failure — 401 for a bad or unrecognised
    token, or whatever status a plugin's ``AuthError`` carries (503 where a
    plugin's token backend is unreachable and it therefore cannot tell).
    """
    token = _extract_bearer(request)

    for plugin in plugins:
        if any(token.startswith(p) for p in plugin.prefixes):
            try:
                principal = await plugin.validate(token)
            except AuthError as e:
                raise HTTPException(status_code=e.status_code, detail=e.detail) from e
            if principal is None:
                raise HTTPException(status_code=401, detail="Invalid token")
            logger.debug("Authenticated %s via %s (prefix)", principal.username, plugin.name)
            return principal

    if strict_prefix_mode:
        raise HTTPException(status_code=401, detail="Invalid token")

    for plugin in plugins:
        if plugin.prefixes:
            continue
        try:
            principal = await plugin.validate(token)
        except AuthError as e:
            raise HTTPException(status_code=e.status_code, detail=e.detail) from e
        if principal is not None:
            logger.debug("Authenticated %s via %s", principal.username, plugin.name)
            return principal

    raise HTTPException(status_code=401, detail="Invalid token")
