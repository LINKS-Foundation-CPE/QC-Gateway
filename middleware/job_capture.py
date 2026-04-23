"""Helpers for uploading submitted payloads.

Generic — vendor-specific response parsing lives in the vendor plugin,
and site-specific reporting lives in the site plugin.
"""

import json
import logging

logger = logging.getLogger(__name__)


def upload_submitted_circuit(uploader, request_body: bytes, username: str, jobid: str) -> str:
    """Upload submitted payload to MinIO and return the object URL.

    If the body is valid JSON it is uploaded with ``upload_json`` to avoid
    double-escaping on retrieval. Otherwise raw bytes are uploaded.
    """
    path = f"{username}/{jobid}/submitted.json"
    try:
        body_text = request_body.decode()
        obj = json.loads(body_text)
        return uploader.upload_json(obj, path)
    except Exception:
        return uploader.upload_bytes(request_body, path)
