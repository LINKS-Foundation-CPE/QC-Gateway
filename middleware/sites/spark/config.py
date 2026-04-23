"""SPARK-specific configuration settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class SparkSettings(BaseSettings):
    """Settings specific to the SPARK site plugin."""

    PORTAL_API_HOST: str = "http://host.docker.internal:8500"
    BASE_DOMAIN: str | None = None

    model_config = {"extra": "ignore"}
