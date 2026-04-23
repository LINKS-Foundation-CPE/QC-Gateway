"""IQM-specific request body parsing.

Extracts shots, circuit count, project name, and job type from IQM
submission payloads.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def _to_text(request_body: object) -> str:
    """Normalize input body to a text string."""
    if request_body is None:
        return ""
    if isinstance(request_body, bytes):
        try:
            return request_body.decode()
        except Exception:
            return ""
    if isinstance(request_body, str):
        return request_body
    try:
        return str(request_body)
    except Exception:
        return ""


def extract_shots_from_body(request_body) -> int | None:
    """Traverse the JSON request body and return the first 'shots' value found as an int.

    Prefer a top-level integer ``shots`` per the IQM API schema.
    Returns None if not found or if the value cannot be converted to a positive int.
    """
    raw = _to_text(request_body)
    if not raw:
        return None
    try:
        body_json = json.loads(raw)
    except Exception:
        return None

    if isinstance(body_json, dict) and "shots" in body_json:
        val = body_json.get("shots")
        try:
            if isinstance(val, str):
                val = val.strip()
            shots_int = int(val)
            if shots_int > 0:
                return shots_int
            return None
        except Exception:
            return None

    stack = [body_json]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if "shots" in current:
                val = current.get("shots")
                try:
                    if isinstance(val, str):
                        val = val.strip()
                    shots_int = int(val)
                    if shots_int > 0:
                        return shots_int
                    return None
                except Exception:
                    return None
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)

    return None


def count_circuits_in_body(request_body) -> int:
    """Best-effort count of circuits in the submitted body.

    The IQM API expects the body to be a JSON list of circuit objects
    or an object with a 'circuits' list.
    """
    raw = _to_text(request_body)
    if not raw:
        return 0

    try:
        body_json = json.loads(raw)
    except Exception:
        try:
            inner = json.loads(raw)
            body_json = json.loads(inner) if isinstance(inner, str) else inner
        except Exception:
            body_json = []

    if isinstance(body_json, list):
        return len(body_json)

    if isinstance(body_json, dict):
        circuits = body_json.get("circuits")
        if isinstance(circuits, list):
            return len(circuits)

        for v in body_json.values():
            if isinstance(v, list):
                if all(isinstance(x, dict) and "instructions" in x for x in v):
                    return len(v)

    return 0


def extract_project_from_metadata(request_body) -> str | None:
    """Locate and return a ``project`` value inside nested ``metadata``."""
    try:
        body_json = json.loads(request_body.decode()) if request_body else {}
        stack = [body_json]
        metadata = None
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                if "metadata" in current:
                    metadata = current["metadata"]
                    break
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
        if isinstance(metadata, dict) and "project" in metadata:
            return metadata["project"]
    except Exception:
        pass
    return None


def classify_job_type(path: str) -> str:
    """Determine job type based on the IQM request path."""
    if path.startswith("/api/v1/jobs/default/sweep"):
        return "sweep"
    elif path.startswith("/api/v1/jobs/default/circuit"):
        return "circuit"
    return ""
