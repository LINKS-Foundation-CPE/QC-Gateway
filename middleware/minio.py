"""MinIO S3-compatible object storage uploader.

This module provides a simple interface for uploading files to MinIO, an
S3-compatible object storage server. It handles JSON objects, raw bytes,
and HTML links, and constructs the full URLs for the uploaded objects.

Note for new developers:
- MinIO is used for storing job artifacts and submitted circuits.
- The `S3Uploader` class provides methods for uploading different types of data.
- The module ensures the bucket exists before uploading and constructs the full URL for the uploaded object.
"""

import io
import json
import logging
import os

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)


class S3Uploader:
    def __init__(
        self,
        minio_server_url: str | None = None,
        bucket_name: str | None = None,
        app_user: str | None = None,
        app_password: str | None = None,
    ):
        # Always use environment variables or explicit arguments
        minio_server_url = minio_server_url or os.getenv(
            "MINIO_SERVER_URL", "https://store.qtest.linksfoundation.com"
        )
        bucket_name = bucket_name or os.getenv("BUCKET_NAME", "minio-job-data")
        app_user = app_user or os.getenv("APP_USER", "")
        app_password = app_password or os.getenv("APP_PASSWORD", "")

        self.minio_server_url = minio_server_url
        self.bucket_name = bucket_name
        self.client = Minio(
            minio_server_url.replace("https://", "").replace("http://", ""),
            access_key=app_user,
            secret_key=app_password,
            secure=minio_server_url.startswith("https://"),
        )

    def ensure_bucket(self):
        if not self.client.bucket_exists(self.bucket_name):
            self.client.make_bucket(self.bucket_name)

    def upload_json(self, obj, object_name):
        self.ensure_bucket()
        data = json.dumps(obj).encode("utf-8")
        data_stream = io.BytesIO(data)
        self.client.put_object(
            self.bucket_name,
            object_name,
            data_stream,
            length=len(data),
            content_type="application/json",
        )
        # Construct the full URL
        url = f"{self.minio_server_url}/{self.bucket_name}/{object_name}"
        return url

    def upload_links_as_html(
        self, links_dict: dict, object_name: str, title: str = "Job artifacts"
    ):
        """
        Converts a dictionary of {label: url} into an HTML page and uploads it to S3.

        :param links_dict: Dictionary with keys as labels and values as URLs
        :param object_name: Name of the HTML file in S3 (e.g., 'links/index.html')
        :param title: Page title / heading to show at top of generated HTML
        :return: Full URL of the uploaded HTML file
        """
        self.ensure_bucket()

        # Generate HTML content
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset=\"UTF-8\">
    <title>{title}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h2 {{ color: #333; }}
        ul {{ list-style-type: none; padding: 0; }}
        li {{ margin-bottom: 8px; }}
        a {{ text-decoration: none; color: #007BFF; }}
        a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <h2>{title}</h2>
    <ul>
"""
        # Add each link as a list item
        for label, url in links_dict.items():
            safe_label = label.replace("_", " ").title()
            html_content += f'        <li><a href="{url}" target="_blank">{safe_label}</a></li>\n'

        # Close HTML tags
        html_content += """    </ul>
    </body>
    </html>"""

        # Upload the HTML content
        data = html_content.encode("utf-8")
        data_stream = io.BytesIO(data)
        self.client.put_object(
            self.bucket_name, object_name, data_stream, length=len(data), content_type="text/html"
        )

        return f"{self.minio_server_url}/{self.bucket_name}/{object_name}"

    def upload_bytes(
        self, data: bytes, object_name: str, content_type: str = "application/octet-stream"
    ) -> str:
        """
        Upload raw bytes to S3/MinIO.

        :param data: Raw bytes to upload
        :param object_name: Destination object path within the bucket
        :param content_type: MIME type of the content (e.g., 'application/zip')
        :return: Full URL of the uploaded object
        """
        self.ensure_bucket()
        data_stream = io.BytesIO(data)
        self.client.put_object(
            self.bucket_name,
            object_name,
            data_stream,
            length=len(data),
            content_type=content_type,
        )
        return f"{self.minio_server_url}/{self.bucket_name}/{object_name}"


# Example usage:
if __name__ == "__main__":
    try:
        uploader = S3Uploader()
        # Example: upload a dict as JSON
        test_data = {"hello": "world"}
        object_name = "test-object.json"
        url = uploader.upload_json(test_data, object_name)
        logger.info("Uploaded to: %s", url)
    except S3Error as exc:
        logger.exception("MinIO upload error: %s", exc)
