"""The S3 client's endpoint and the URL in handed-out links are separate."""

from middleware.minio import S3Uploader


def _uploader(monkeypatch, **env):
    for key in ("S3_ENDPOINT_URL", "MINIO_INTERNAL_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    up = S3Uploader(
        minio_server_url="https://127.0.0.1:9000",
        bucket_name="job-data",
        app_user="u",
        app_password="p",
    )
    monkeypatch.setattr(up.client, "put_object", lambda *a, **k: None)
    monkeypatch.setattr(up.client, "bucket_exists", lambda *a, **k: True)
    return up


def test_without_an_internal_url_the_client_uses_the_public_one(monkeypatch):
    up = _uploader(monkeypatch)
    assert up.client._base_url.host == "127.0.0.1:9000"
    assert up.client._base_url.is_https


def test_an_internal_url_moves_the_client_but_not_the_links(monkeypatch):
    up = _uploader(monkeypatch, S3_ENDPOINT_URL="http://rustfs:9000")
    assert up.client._base_url.host == "rustfs:9000"
    assert not up.client._base_url.is_https
    assert up.upload_json({"a": 1}, "x/y.json") == "https://127.0.0.1:9000/job-data/x/y.json"


def test_the_legacy_minio_internal_url_is_not_read(monkeypatch):
    """Early .env files carry MINIO_INTERNAL_URL=minio-job-data; it must stay inert."""
    up = _uploader(monkeypatch, MINIO_INTERNAL_URL="minio-job-data")
    assert up.client._base_url.host == "127.0.0.1:9000"


def test_an_endpoint_without_a_scheme_is_ignored(monkeypatch):
    up = _uploader(monkeypatch, S3_ENDPOINT_URL="rustfs:9000")
    assert up.client._base_url.host == "127.0.0.1:9000"
