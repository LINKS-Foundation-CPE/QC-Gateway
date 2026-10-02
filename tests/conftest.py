"""Shared test setup.

``middleware.config`` instantiates ``Settings()`` at import time and several
fields have no defaults, so the environment must be populated before any
``middleware.*`` import happens in the tests.
"""

import os

os.environ.setdefault("MINIO_SERVER_URL", "http://minio.test:9000")
os.environ.setdefault("BUCKET_NAME", "test-bucket")
os.environ.setdefault("APP_USER", "test-user")
os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("KEYCLOAK_JWKS_URL", "http://keycloak.test/jwks")
os.environ.setdefault("KEYCLOAK_ISSUER", "http://keycloak.test/realms/test")
os.environ.setdefault("AUDIENCE", "test-audience")
