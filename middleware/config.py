"""Core proxy configuration.

Contains settings that are generic to the proxy middleware regardless
of which vendor or site plugin is active. Vendor-specific settings
(e.g., IQM_SERVER_TOKEN, ROLE_ROUTES) live in their respective plugin
config modules. Site-specific settings (e.g., PORTAL_API_HOST) live in
their respective plugin config modules.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Plugin selection
    VENDOR_PLUGIN: str = "iqm"
    SITE_PLUGIN: str = "spark"

    # Operational mode: production, authentication, reporting, maintenance
    MIDDLEWARE_MODE: str = "production"

    # Authentication settings (Keycloak/JWT)
    KEYCLOAK_ISSUER: str | None = None
    KEYCLOAK_JWKS_URL: str | None = None
    AUDIENCE: str | None = None

    # Upstream API settings
    MACHINE_URL: str | None = None
    UPSTREAM_TIMEOUT: int = 30
    VERIFY_UPSTREAM_SSL: bool = False

    # Public-facing frontend URL used for CORS origin whitelisting.
    FRONTEND_URL: str | None = None

    # MinIO/S3 settings
    MINIO_SERVER_URL: str
    BUCKET_NAME: str
    APP_USER: str
    APP_PASSWORD: str

    # Logging settings
    LOG_LEVEL: str = "INFO"

    # Redis settings for distributed counters
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None

    # Concurrency limits
    MAX_CONCURRENT_SHOTS: int = 2500000
    MAX_CONCURRENT_SWEEPS: int = 10

    # Job-reporter / background-worker settings
    JOB_REPORTER_INTERVAL: int = 60
    JOB_REPORTER_HTTP_TIMEOUT: float = 30.0
    JOB_REPORTER_VERIFY_TLS: bool | None = None
    JOB_REPORTER_MAX_ERRORS: int = 10
    JOB_REPORTER_BACKOFF_FACTOR: float = 2.0
    JOB_REPORTER_MAX_BACKOFF: int = 300

    # Prometheus settings
    METRICS_POLL_INTERVAL_SECONDS: int = 5


settings = Settings()
