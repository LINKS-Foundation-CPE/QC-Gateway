"""Artifact storage must not corrupt what the machine returned.

The machine's sweep results and payload are protobuf. They used to be stored
through ``resp.text``, which decodes with ``errors="replace"``: every byte
that is not valid UTF-8 became U+FFFD and was gone. On a real sweep-results
artifact that destroyed 38% of the bytes, leaving a file no parser can read.
"""

import json

import httpx

from middleware.artifacts import upload_artifact_from_response


class FakeUploader:
    def __init__(self):
        self.objects = {}

    def upload_json(self, obj, object_name):
        self.objects[object_name] = ("application/json", json.dumps(obj).encode())
        return f"https://store.test/{object_name}"

    def upload_bytes(self, data, object_name, content_type="application/octet-stream"):
        self.objects[object_name] = (content_type, data)
        return f"https://store.test/{object_name}"


def _stored(uploader, url):
    return uploader.objects[url.removeprefix("https://store.test/")]


# Bytes that are deliberately not valid UTF-8: 0x80 and 0xFF are continuation
# and invalid bytes, which is what a protobuf length prefix or float payload
# routinely contains.
BINARY = b"\x0a$01a0854f\x12\x80\x3e\xff\xfe\x00\x07QB1__tt\x80\x80"


def test_binary_artifact_is_stored_byte_for_byte():
    uploader = FakeUploader()
    resp = httpx.Response(200, content=BINARY, headers={"content-type": "application/x-protobuf"})

    url = upload_artifact_from_response(uploader, "u@example.org", "job-1", "sweep_results", resp)

    content_type, data = _stored(uploader, url)
    assert data == BINARY, "the machine's bytes must survive storage unchanged"
    assert content_type == "application/x-protobuf"
    assert url.endswith("sweep_results.bin"), "a protobuf must not be named .json"


def test_binary_artifact_is_not_decoded_as_text():
    """The specific regression: U+FFFD must never appear in a stored artifact."""
    uploader = FakeUploader()
    resp = httpx.Response(200, content=BINARY)

    url = upload_artifact_from_response(uploader, "u@example.org", "job-1", "payload", resp)

    _, data = _stored(uploader, url)
    assert b"\xef\xbf\xbd" not in data, "U+FFFD in the store means bytes were lost"
    assert len(data) == len(BINARY)


def test_json_artifact_still_stored_as_json():
    uploader = FakeUploader()
    timeline = [{"source": "iqm-server", "status": "created"}]
    resp = httpx.Response(200, json=timeline, headers={"content-type": "application/json"})

    url = upload_artifact_from_response(uploader, "u@example.org", "job-1", "timeline", resp)

    content_type, data = _stored(uploader, url)
    assert url.endswith("timeline.json")
    assert content_type == "application/json"
    assert json.loads(data) == timeline


def test_json_body_with_a_wrong_content_type_is_still_json():
    """Dispatch is on whether the body parses, not on what the header claims."""
    uploader = FakeUploader()
    resp = httpx.Response(
        200, content=b'{"status": "ready"}', headers={"content-type": "application/octet-stream"}
    )

    url = upload_artifact_from_response(uploader, "u@example.org", "job-1", "status", resp)

    assert url.endswith("status.json")
    assert json.loads(_stored(uploader, url)[1]) == {"status": "ready"}
