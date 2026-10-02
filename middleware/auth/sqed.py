"""Sqed authentication plugin: per-job tokens on a Redis bus.

For deployments where submissions come from a batch scheduler rather than
from a person. A SLURM SPANK plugin mints a token when a job starts and
publishes it to Redis; the client submits with that token instead of an OIDC
one, and this plugin turns it back into an identity.

The record layout, which this plugin only ever **reads** — the ``qpu:*``
namespace belongs to whatever provisions the tokens:

    key    qpu:token:<token>          (a hash)
    field  <slurm job id>
    value  JSON: {"uid", "username", "account", "n_licenses",
                  "issued_at", "expires_at"}

One field per job currently backing the token, because a job array or a
heterogeneous job shares a single token across several jobs whose fields
appear (prolog) and disappear (epilog, or per-field TTL) independently. The
token is therefore valid while **at least one** live field remains, and the
concurrent budget is the sum over live fields — not a property of the token
itself.

``account`` is the SLURM account the job was charged to, i.e. the project in
HPC terms, carried in ``metadata`` for a site plugin to use; the cluster is
the authority on it, unlike anything the client puts in its own metadata.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from middleware.plugins.datatypes import Principal
from middleware.plugins.interfaces import AuthError

logger = logging.getLogger(__name__)


class SqedAuthPlugin:
    """Maps scheduler-provisioned Redis token records to a ``Principal``."""

    name = "sqed"

    def __init__(self, settings: Any) -> None:
        from middleware.auth.sqed_config import SqedSettings
        from middleware.job_counters import get_redis_client

        self._settings = SqedSettings()
        self._redis = get_redis_client()
        self._roles_cache: dict[str, tuple[float, list[str]]] = {}
        self._http: httpx.AsyncClient | None = None
        if self._redis is None:
            logger.warning("sqed auth: Redis unavailable at startup; tokens will be rejected")

    @property
    def prefixes(self) -> list[str]:
        return [self._settings.SQED_TOKEN_PREFIX]

    async def validate(self, token: str) -> Principal | None:
        if self._redis is None:
            # Redis may have come up after the gateway did; retry once per
            # request rather than requiring a restart to recover.
            from middleware.job_counters import get_redis_client

            self._redis = get_redis_client()
            if self._redis is None:
                raise AuthError("token backend unavailable", status_code=503)

        try:
            fields = self._redis.hgetall(f"qpu:token:{token}")
        except Exception as e:
            # 503, not 401: the token may well be valid and we cannot tell.
            # Authentication still fails closed — the request is refused —
            # but the status says whose problem it is.
            logger.error("sqed auth: Redis error: %s", e)
            raise AuthError("token backend unavailable", status_code=503) from e

        if not fields:
            raise AuthError("token not found or expired")

        now = int(time.time())
        live: list[dict[str, Any]] = []
        for job_id, raw in fields.items():
            try:
                record = json.loads(raw)
            except (TypeError, ValueError):
                # One malformed field must not condemn a token whose other
                # jobs are fine; it is logged and skipped.
                logger.error("sqed auth: malformed record for job %s", job_id)
                continue
            # Per-field expiry in Redis is authoritative; this only guards
            # against TTL drift between the provisioner and this reader.
            if int(record.get("expires_at", now + 1)) > now:
                record["_job_id"] = str(job_id)
                live.append(record)

        if not live:
            raise AuthError("token expired")

        uid = live[0].get("uid")
        username = live[0].get("username") or str(uid)
        account = live[0].get("account") or None
        roles = [r.strip() for r in self._settings.SQED_DEFAULT_ROLES.split(",") if r.strip()]
        for role in await self._lookup_roles(username):
            if role not in roles:
                roles.append(role)

        return Principal(
            auth_source=self.name,
            uid=str(uid),
            username=username,
            roles=roles,
            metadata={
                "job_ids": sorted(r["_job_id"] for r in live),
                "n_licenses": sum(int(r.get("n_licenses", 0)) for r in live),
                "account": account,
                "uid": uid,
                "issued_at": min(int(r.get("issued_at", 0)) for r in live),
                "expires_at": max(int(r.get("expires_at", 0)) for r in live),
            },
        )

    async def _lookup_roles(self, username: str) -> list[str]:
        """Per-user roles from the portal backend, on top of the defaults.

        Fail-open, deliberately: this lookup only ever ADDS roles, so a
        backend outage costs a user their extra privileges but does not cost
        them the ability to submit. Authentication has already succeeded by
        this point — the scheduler vouched for the account — and failing
        closed here would let a portal outage stop a running cluster.
        """
        url = self._settings.SQED_ROLES_LOOKUP_URL.rstrip("/")
        if not url:
            return []

        now = time.monotonic()
        cached = self._roles_cache.get(username)
        if cached and cached[0] > now:
            return cached[1]

        roles: list[str] = []
        try:
            if self._http is None:
                self._http = httpx.AsyncClient(
                    timeout=2.0, verify=self._settings.SQED_ROLES_VERIFY_TLS
                )
            resp = await self._http.get(f"{url}/{username}")
            if resp.status_code == 200:
                fetched = resp.json().get("roles", [])
                roles = [r for r in fetched if isinstance(r, str)]
            elif resp.status_code != 404:
                # 404 is a known answer: no such user, so no extra roles.
                logger.warning("sqed auth: roles lookup for %s got %s", username, resp.status_code)
        except Exception as e:
            logger.warning("sqed auth: roles lookup failed for %s: %s", username, e)

        # Negative results are cached too, so a backend that is down does not
        # get a request per submission.
        self._roles_cache[username] = (now + self._settings.SQED_ROLES_CACHE_TTL, roles)
        return roles
