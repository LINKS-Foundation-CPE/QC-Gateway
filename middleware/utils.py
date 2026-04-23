"""Generic utility helpers shared across the proxy middleware.

Contains only framework-agnostic helpers for response filtering and building.
Vendor-specific parsing lives in the vendor plugin.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


def filter_response_headers(headers: dict[str, Any]) -> dict[str, str]:
    """Filter out hop-by-hop and problematic headers from an httpx response."""
    return {
        k: v
        for k, v in headers.items()
        if k.lower()
        not in [
            "content-encoding",
            "transfer-encoding",
            "content-length",
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "upgrade",
        ]
    }


def build_json_response(
    upstream_response: httpx.Response,
    status_code: int | None = None,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a JSONResponse from an upstream httpx response."""
    response_headers = filter_response_headers(upstream_response.headers)
    response_headers["Content-Type"] = "application/json; charset=utf-8"

    if extra_headers:
        response_headers.update(extra_headers)

    try:
        content = upstream_response.json()
    except Exception:
        content = {"error": "Invalid JSON response from upstream"}

    return JSONResponse(
        content=content,
        status_code=status_code or upstream_response.status_code,
        headers=response_headers,
    )


def build_raw_response(
    upstream_response: httpx.Response,
    status_code: int | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    """Build a raw Response from an upstream httpx response."""
    response_headers = filter_response_headers(upstream_response.headers)

    if extra_headers:
        response_headers.update(extra_headers)

    return Response(
        content=upstream_response.content,
        status_code=status_code or upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type", None),
    )
