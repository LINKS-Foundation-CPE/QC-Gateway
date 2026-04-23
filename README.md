# QC Gateway

An authenticating, auditing reverse proxy for quantum computer HTTP APIs.

The proxy sits between end users (or client SDKs) and a quantum backend and
adds the production concerns that vendor APIs typically don't provide:
JWT authentication, role-based authorization, per-user concurrency limits,
job accounting, artifact archival, and Prometheus metrics. Vendor-specific
and site-specific behavior is isolated behind plugin interfaces so the core
can be reused across different machines and deployments.

> Status: Developed at the Quantum Computing and Simulation Lab of LINKS
> Foundation and used in production on IQM-based quantum hardware. Open-
> sourced so other projects can reuse the core and contribute plugins for
> different vendors and sites.

## Transparent to existing SDKs

QC Gateway preserves the upstream vendor API on the wire — URL paths,
request/response shapes, status codes, and headers all flow through
unchanged. Existing vendor client SDKs and CLIs (e.g. `iqm-client`) work **without modification**.
## Features

- **Drop-in compatible with existing SDKs** — the gateway preserves the
  vendor API verbatim, so clients point at the gateway URL and continue
  to work unchanged. No custom SDK, no API translation.
- **JWT authentication** against any OIDC issuer (tested with Keycloak).
- **Role-based access control** driven by a vendor-declared route table.
- **Per-user concurrency limiting** backed by Redis counters (shots and
  sweep circuits are both tracked).
- **Job accounting** — submitted payloads are captured in S3-compatible
  object storage (MinIO) and recorded in PostgreSQL.
- **Background job reconciliation** — a separate worker polls upstream job
  status, uploads artifacts, and reports final state to the site's portal.
- **Prometheus metrics** — per-user and global gauges for queued / active
  jobs, suitable for Grafana dashboards.
- **Four operation modes** (`production`, `authentication`, `reporting`,
  `maintenance`) selectable by environment variable, so the same image
  can be deployed for pre-prod validation, mirror mode, or planned downtime.
- **Pluggable vendor and site layers** — add a new backend or a new
  authorization/reporting target without touching the core.

## Architecture at a glance

```
client SDK / CLI
             │
             ▼
    ┌──────────────────┐        ┌────────────────────┐
    │  Middleware core │◄──────▶│   Site plugin      │
    │  (auth, RBAC,    │        │ (authorize,        │
    │   concurrency)   │        │  report jobs,      │
    │                  │        │  billing)          │
    │                  │        └────────────────────┘
    │                  │        ┌────────────────────┐
    │                  │◄──────▶│   Vendor plugin    │
    └─────────┬────────┘        │ (parse, proxy,     │
              │                 │  poll, artifacts)  │
              ▼                 └─────────┬──────────┘
        Redis / MinIO /                   │
        PostgreSQL                        ▼
                                   Quantum backend
                                     (e.g. IQM)
```

Currently available plugins:

- **Vendor:** `iqm` — IQM Cocos-style APIs.
- **Site:** `spark` — the SPARK project's billing/authorization portal.

Both are reference implementations. Swapping either is a matter of writing
a class that implements the relevant `Protocol` and registering it. See
[DOCUMENTATION.md](docs/DOCUMENTATION.md) and [MODULARIZATION.md](docs/MODULARIZATION.md)
for the full interface contracts and extension walk-throughs.

## Quick start

Requirements: Docker + Docker Compose, a working upstream quantum endpoint,
and an OIDC issuer for JWT validation.

```bash
cp env.example .env        # fill in MACHINE_URL, IQM_SERVER_TOKEN, etc.
./run_middleware.sh production
```

`run_middleware.sh` is a thin convenience wrapper around
`docker compose up` that exports `MIDDLEWARE_MODE`. You can also bring the
stack up directly:

```bash
MIDDLEWARE_MODE=production docker compose up -d
```

### Operation modes

| Mode             | Auth | Authorization | Concurrency limits | Reporting | Proxying |
|------------------|------|---------------|--------------------|-----------|----------|
| `production`     | ✅   | ✅            | ✅                 | ✅        | ✅       |
| `authentication` | ✅   | —             | —                  | —         | ✅       |
| `reporting`      | ✅   | —             | —                  | ✅        | ✅       |
| `maintenance`    | —    | —             | —                  | —         | ❌ (503) |

Set with `MIDDLEWARE_MODE=<mode>` in `.env` or via `run_middleware.sh <mode>`.

### Plugin selection

```bash
VENDOR_PLUGIN=iqm      # default
SITE_PLUGIN=spark      # default
```

## Documentation

- [DOCUMENTATION.md](docs/DOCUMENTATION.md) — application overview, request flow,
  configuration reference, and how to add new plugins.
- [MODULARIZATION.md](docs/MODULARIZATION.md) — plugin architecture deep dive,
  interface contracts, and direct-use examples for the generic modules
  (`authorization`, `concurrency`, `reporting`).
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup, coding style,
  how to run linters and tests, and the contribution workflow.
- [CLAUDE.md](CLAUDE.md) — orientation for AI assistants working on this
  codebase (also a useful cheat sheet for new human contributors).

## Project layout

```
middleware/
├── main.py              # FastAPI app and the proxy middleware
├── config.py            # Core settings (Pydantic)
├── authentication.py    # JWT / Keycloak
├── authorization.py     # RoleAuthorizationChecker (RBAC)
├── concurrency.py       # Redis-backed ConcurrencyLimiter
├── db.py                # PostgreSQL job log
├── minio.py             # S3 uploader
├── reporting.py         # Generic JobReporter / SyncJobReporter
├── job_reporter.py      # Background reconciliation worker
├── plugins/
│   ├── interfaces.py    # VendorPlugin, SitePlugin protocols
│   ├── datatypes.py     # Shared dataclasses
│   └── loader.py        # Plugin registry
├── vendors/iqm/         # IQM vendor plugin
└── sites/spark/         # SPARK site plugin
```

## Contributing

Contributions — especially new vendor/site plugins — are welcome. Please
read [CONTRIBUTING.md](CONTRIBUTING.md) first; at minimum, install the
development toolchain:

```bash
pip install -r requirements-dev.txt
pre-commit install
```

and make sure `ruff check .`, `ruff format --check .`, and `mypy` pass
before opening a pull request.

## Licensing

QC Gateway is licensed under the **European Union Public Licence v. 1.2
(EUPL-1.2)** — see [LICENSE](LICENSE).

EUPL-1.2 is an OSI- and FSF-approved copyleft licence maintained by the
European Commission. It is compatible with GPL (v2 and v3), LGPL, AGPL,
MPL, CeCILL, and several other major licences through the EUPL
compatibility list, so code from those licences can be combined with
QC Gateway without licence conflicts.

### What this means in practice

- **Running QC Gateway, as-is or modified, inside your own infrastructure
  for your own users** — no disclosure obligations. EUPL does not close
  the SaaS / internal-use carve-out, so institutional deployments of the
  gateway for your own operators and end users are unencumbered.
- **Distributing a modified version** — for example, forking the code
  and shipping it as a product — your modifications must be released
  under the EUPL or a compatible licence, and the source must be made
  available to the downstream recipient.
- **Third-party plugins** run in the same Python process as the core and
  are, when distributed together with the gateway, derivative works. A
  bundled distribution (plugin + gateway) must therefore be released
  under the EUPL. A plugin you develop and deploy on your own
  infrastructure without redistribution triggers no obligation, so
  proprietary in-house plugins remain entirely possible.

### Why EUPL

EUPL strikes the balance appropriate for a publicly-funded project
serving quantum computing operators: it protects the gateway from
proprietary closed-source forks, while leaving internal deployments and
in-house plugins unencumbered — the principal intended use case. It
also fits the project's European public-sector funding context, being
the European Commission's recommended licence for such software, and
its compatibility list covers every major copyleft licence a downstream
project is likely to use.

Nothing in this section is legal advice — consult the full
[LICENSE](LICENSE) text, and your own counsel, for any concrete case.
