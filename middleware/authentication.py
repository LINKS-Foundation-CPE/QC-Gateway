"""User authentication via JWT tokens validated against an OIDC JWKS endpoint.

Verifies incoming ``Authorization: Bearer <token>`` headers against a Keycloak
(or any OIDC-compliant) JWKS endpoint using RS256. Signing keys are fetched
lazily and cached by :class:`jwt.PyJWKClient`.
"""

import logging

import jwt
from fastapi import HTTPException, Request
from jwt import InvalidTokenError, PyJWKClient, PyJWKClientError
from pydantic import BaseModel

from middleware.config import Settings

logger = logging.getLogger(__name__)
settings = Settings()


class User(BaseModel):
    id: str
    username: str
    roles: list[str] = []


_jwks_client = PyJWKClient(settings.KEYCLOAK_JWKS_URL, cache_keys=True)


async def get_current_user(request: Request) -> User:
    """Validate the ``Authorization`` header and return the authenticated user.

    Raises ``HTTPException(401)`` on any verification failure (missing header,
    signature mismatch, expired token, wrong audience/issuer, JWKS fetch error).
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    token = auth.removeprefix("Bearer ")
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token).key
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.AUDIENCE,
            issuer=settings.KEYCLOAK_ISSUER,
        )
    except (InvalidTokenError, PyJWKClientError) as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e!s}") from e

    return User(
        id=payload["sub"],
        username=payload.get("preferred_username", ""),
        roles=payload.get("realm_access", {}).get("roles", []),
    )
