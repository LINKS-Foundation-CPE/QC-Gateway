# Development deployment — no FQDN, self-signed TLS

`docker-compose.dev.yaml` runs the QC Gateway stack on a bare IP with
self-signed HTTPS: no SWAG, no Let's Encrypt, no DNS records. It is meant for
full-fledged development of several components at once (gateway, quantum-api,
dashboard, Keycloak) on a throwaway VM or laptop — unlike the official
dev/staging environment, which is reserved for integration testing.

**Do not use this in production**: the certificate is self-signed (clients
must explicitly trust it) and the object store is anonymously readable by key, by design.

## Architecture

A single self-signed certificate (SANs: host IP, `127.0.0.1`, `localhost`,
`host.docker.internal`) is generated in the **keycloak-deployment** repo and
shared by everything:

- a `tls-proxy` nginx terminates HTTPS for the gateway, dashboard, object store
  (RustFS) and the jobs portal — the services themselves stay plain HTTP inside the
  compose network (so e.g. `rustfs-init/bootstrap.sh` is the same in both);
- **quantum-api** serves HTTPS natively (built-in `SSL_CERT`/`SSL_KEY`,
  cert mounted via the explicit `docker-compose.tls-dev.yaml` overlay in
  that repo);
- **Keycloak** terminates its own HTTPS (dev compose in keycloak-deployment).

| Port | Service (all HTTPS) |
|---|---|
| 8000 | QC Gateway — point `IQM_SERVER_URL` here |
| 8080 | quantum-dashboard dev server (optional profile) |
| 8500 | quantum-api backend (own repo, native TLS) |
| 8843 | Keycloak (own repo) |
| 8901 | Object store console (RustFS) |
| 8940 | Jobs results portal |
| 9000 | Object store S3 API (artifact links) |

## Differences from the production compose

| Production (`docker-compose.yaml`) | Dev (`docker-compose.dev.yaml`) |
|---|---|
| SWAG: Let's Encrypt certs + subdomain vhosts (`spark.*`, `api.*`, …) | `tls-proxy` nginx: self-signed cert + port-based routing |
| Needs FQDN (`BASE_DOMAIN`, `EXTRA_DOMAINS`, …) | Needs only the host IP |
| `AUTH_URL`-derived Keycloak settings | `KEYCLOAK_ISSUER`/`KEYCLOAK_JWKS_URL` set directly in `.env.dev` |
| Results portal at `https://jobs.${BASE_DOMAIN}` | `JOBS_PORTAL_URL` setting → jobs-portal container on `:8940` |
| restic / volumes-backup | Not started |

The middleware code path is identical: only entry-point plumbing changes.

## Quick start

### 0. Keycloak (keycloak-deployment repo, sibling checkout)

```bash
cd ../keycloak-deployment
./gen-dev-certs.sh <host-ip>                 # shared self-signed cert -> certs/
cp secret.env.example secret_conf.dev.env    # set KC_BOOTSTRAP_ADMIN_PASSWORD,
                                             # KC_DB_PASSWORD == POSTGRES_PASSWORD
KC_DEV_HOSTNAME=<host-ip> docker compose -f docker-compose.dev.yaml up -d
./bootstrap-dev-realm.sh                     # realm cortex, client test-frontend,
                                             # roles, testadmin/testuser@example.com
                                             # (passwords -> dev-credentials.env)
```

### 1. Gateway

```bash
cd ../nginx-reverse-proxy
cp env.dev.example .env.dev
# edit .env.dev: host IP, MACHINE_URL + IQM_SERVER_TOKEN (mock device or QC)
docker compose -f docker-compose.dev.yaml up -d --build
```

Smoke test (`--cacert` proves the chain; `-k` also fine for dev):

```bash
C=../keycloak-deployment/certs/keycloak.crt
curl --cacert $C https://<host>:8000/health
curl --cacert $C https://<host>:8000/proxy-config
```

### 2. quantum-api (full production mode)

```bash
cd ../quantum-api
cp .env.example .env
# edit .env:
#   KEYCLOAK_BASE_URL=https://<host>:8843/auth
#   BACKEND_SECRET / POSTGRES_PASSWORD: openssl rand -hex 32
#   DB_HOST=quantum-api-db
#   CORS_ORIGIN=https://<host>:8080
#   SSL_CERT=/certs/keycloak.crt
#   SSL_KEY=/certs/keycloak.key
docker compose -f docker-compose.yaml -f docker-compose.tls-dev.yaml up -d --build
# (the tls-dev overlay mounts the certs; it is never applied implicitly)

# seed org/project/test-users through the real API:
source ../keycloak-deployment/dev-credentials.env
KEYCLOAK_URL=https://<host>:8843/auth \
  ADMIN_PASS="$TESTADMIN_PASSWORD" TEST_PASS="$TESTUSER_PASSWORD" \
  ./scripts/dev-seed.sh

# then switch the gateway to the full loop: MIDDLEWARE_MODE=production in
# .env.dev and recreate fastapi-proxy + reporter.
```

### 3. Dashboard (optional)

```bash
cd ../nginx-reverse-proxy
# uncomment the VITE_* block in .env.dev, then:
docker compose -f docker-compose.dev.yaml --profile dashboard up -d
```

### 4. Browser trust (once per browser)

The cert is self-signed, so visit and accept the warning on each HTTPS origin
the dashboard talks to, in this order:

1. `https://<host>:8843` (Keycloak — login breaks silently without this)
2. `https://<host>:8500` (quantum-api — XHR from the dashboard is blocked without it)
3. `https://<host>:8080` (the dashboard itself)

Artifact/results links (`:9000`, `:8940`) are top-level navigations — the
browser will prompt when you first open one.

Log in with `testadmin@example.com` (admin console) or `testuser@example.com`;
passwords in `keycloak-deployment/dev-credentials.env`. Usernames are
email-shaped on purpose: the jobReport schema requires the gateway-reported
username to be an email, as in production.

### Submitting jobs (unmodified IQM client)

```bash
export IQM_SERVER_URL=https://<host>:8000
# self-signed: export REQUESTS_CA_BUNDLE=<path>/keycloak.crt (or equivalent)
```

## Notes and caveats

- **`.env.dev` is gitignored** (like `.env`). Never commit real tokens.
- `MINIO_SERVER_URL` / `JOBS_PORTAL_URL` are embedded in stored result links;
  changing the host IP later leaves stale links behind.
- The tls-proxy forwards `Host $http_host` (with port) — required by S3 v4
  request signing; plain `$host` strips the port and breaks uploads
  with `SignatureDoesNotMatch`.
- Containers trust the dev cert via `SSL_CERT_FILE` (Python stdlib/httpx/
  minio-py) and `REQUESTS_CA_BUNDLE` (requests) pointing at the mounted
  `/certs/keycloak.crt`; cert dir path overridable via `KEYCLOAK_CERT_PATH`.
- The object store container keeps its production name `rustfs-job-data`,
  so dev and production stacks cannot share a host.
- The gateway containers reach quantum-api at
  `https://host.docker.internal:8500` — that name is in the dev cert SANs.
- Dev containers/volumes are otherwise suffixed `-dev`/`_dev`.
- TLS toward the *machine* is unrelated: `MACHINE_URL` may be HTTPS with its
  own self-signed cert (`VERIFY_UPSTREAM_SSL=false`).
