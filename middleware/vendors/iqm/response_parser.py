"""IQM-specific response parsing.

Extracts job IDs and artifact types from IQM upstream responses.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _recursive_find_key(obj: Any, key: str) -> Any:
    """Walk ``obj`` recursively and return the first value associated with ``key``."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            res = _recursive_find_key(v, key)
            if res is not None:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = _recursive_find_key(item, key)
            if res is not None:
                return res
    return None


def extract_jobid_from_response_text(response_text: str) -> str:
    """Return the ``id`` field from a JSON response body, or empty string."""
    if not response_text:
        return ""
    try:
        obj = json.loads(response_text)
        jobid = _recursive_find_key(obj, "id")
        if isinstance(jobid, str):
            return jobid
    except Exception:
        pass
    return ""


def extract_artifact_types_from_response_text(response_text: str) -> list[str]:
    """Return a list of artifact ``type`` strings from a submission response."""
    if not response_text:
        return []
    try:
        obj = json.loads(response_text)
        if isinstance(obj, dict):
            raw = obj.get("artifacts") or []
            if isinstance(raw, list):
                return [
                    a.get("type")
                    for a in raw
                    if isinstance(a, dict) and isinstance(a.get("type"), str)
                ]
    except Exception:
        pass
    return []
