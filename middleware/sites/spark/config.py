"""SPARK-specific configuration settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class SparkSettings(BaseSettings):
    """Settings specific to the SPARK site plugin."""

    PORTAL_API_HOST: str = "http://host.docker.internal:8500"
    BASE_DOMAIN: str | None = None
    # Full URL of the jobs results portal. When unset, falls back to
    # https://jobs.{BASE_DOMAIN} (the production SWAG vhost). Set explicitly
    # in deployments without a FQDN/TLS (e.g. http://10.0.0.5:8940).
    JOBS_PORTAL_URL: str | None = None

    model_config = {"extra": "ignore"}
