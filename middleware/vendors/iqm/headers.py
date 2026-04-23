"""IQM-specific header building for upstream requests."""

from __future__ import annotations


def build_machine_headers(iqm_server_token: str | None = None) -> dict[str, str]:
    """Return headers for requests to the IQM machine URL.

    Injects ``Authorization: Bearer <token>`` when a token is provided.
    """
    headers: dict[str, str] = {}
    if iqm_server_token:
        t = iqm_server_token.strip()
        if not t.lower().startswith("bearer "):
            t = f"Bearer {t}"
        headers["Authorization"] = t
    return headers
