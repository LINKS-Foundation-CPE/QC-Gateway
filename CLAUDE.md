# CLAUDE.md

Orientation for AI assistants (and new human contributors) working on
**QC Gateway**. Read this before making changes.

## What this project is

An authenticating, auditing reverse proxy for quantum computer HTTP APIs.
The core is framework-agnostic; vendor-specific and site-specific behavior
lives in **plugins** that implement well-defined `Protocol` contracts.

If you're tempted to add vendor- or site-specific logic to the core,
**stop** — it belongs in a plugin.

- Deep dives: [DOCUMENTATION.md](docs/DOCUMENTATION.md), [MODULARIZATION.md](docs/MODULARIZATION.md).
- User-facing overview: [README.md](README.md).
- Development workflow: [CONTRIBUTING.md](CONTRIBUTING.md).

## Architectural rules (non-negotiable)

1. **Core must stay vendor-agnostic and site-agnostic.**
   `middleware/` (outside `vendors/` and `sites/`) may not import from
   `middleware.vendors.*` or `middleware.sites.*`, and may not branch on
   vendor or site names.

2. **All cross-layer data flows through dataclasses in
   [middleware/plugins/datatypes.py](middleware/plugins/datatypes.py).**
   Don't pass raw dicts or vendor-shaped JSON across the plugin boundary —
   add or extend a dataclass instead.

3. **Plugin contracts are `Protocol`s, not base classes.**
   New plugins implement `VendorPlugin` or `SitePlugin` structurally. Don't
   introduce abstract base classes.

4. **Fail open on non-critical paths.**
   Redis, MinIO, and portal failures log and continue; they do not block
   proxying. Only authentication and authorization failures should
   short-circuit a request.

5. **`proxy_and_capture` in [middleware/main.py](middleware/main.py) is
   the canonical request flow.** If you're changing the order of auth /
   RBAC / authorization / concurrency / proxy / accounting, that's a
   significant change — read the full function first and explain the new
   flow in your PR.

## Where things live

```
middleware/
├── main.py              # FastAPI app + proxy middleware (the hot path)
├── config.py            # Core Pydantic Settings
├── authentication.py    # JWT / Keycloak → User
├── authorization.py     # RoleAuthorizationChecker (RBAC)
├── concurrency.py       # ConcurrencyLimiter (Redis)
├── db.py                # PostgreSQL job log
├── minio.py             # S3Uploader
├── artifacts.py         # Generic artifact upload helpers
├── reporting.py         # Generic JobReporter / SyncJobReporter
├── job_capture.py       # Payload capture helper
├── job_counters.py      # Prometheus metrics worker
├── job_reporter.py      # Background reconciliation worker
├── utils.py             # Generic response builders
├── plugins/
│   ├── interfaces.py    # VendorPlugin, SitePlugin (Protocols)
│   ├── datatypes.py     # Shared dataclasses
│   └── loader.py        # Plugin registry + resolution
├── vendors/iqm/         # Reference vendor plugin
└── sites/spark/         # Reference site plugin
```

## When making changes

### Adding a feature to the core

- Does it generalize across vendors/sites? If not, it's a plugin feature.
- Does it need new data at the plugin boundary? Add a field to an existing
  dataclass in [middleware/plugins/datatypes.py](middleware/plugins/datatypes.py),
  or add a new one — don't widen method signatures with ad-hoc parameters.
- Does it change the request flow? Update the Flow section of
  [DOCUMENTATION.md](docs/DOCUMENTATION.md) in the same PR.

### Adding a new vendor or site plugin

Follow the walk-through in
[DOCUMENTATION.md §Extending with New Plugins](docs/DOCUMENTATION.md#extending-with-new-plugins)
and [MODULARIZATION.md](docs/MODULARIZATION.md). You must:

1. Create `middleware/vendors/<name>/` or `middleware/sites/<name>/` with
   a `plugin.py` that satisfies the relevant Protocol.
2. Register it in [middleware/plugins/loader.py](middleware/plugins/loader.py).
3. Keep vendor/site-specific settings in a local `config.py` — do **not**
   add them to `middleware/config.py`.

### Touching configuration

- Core settings → `middleware/config.py`.
- Vendor settings → `middleware/vendors/<name>/config.py`.
- Site settings → `middleware/sites/<name>/config.py`.
- New env var? Add it to `env.example` with a safe default or placeholder.

### Touching docs

The public entry points are README.md (audience: users/evaluators) and
docs/DOCUMENTATION.md (audience: operators/integrators). docs/MODULARIZATION.md is
for plugin authors. Keep each document focused on its audience — don't
cross-pollinate.

## Development workflow

See [CONTRIBUTING.md](CONTRIBUTING.md). In short:

```bash
pip install -r requirements-dev.txt
pre-commit install
ruff check . && ruff format --check . && mypy
```

Before opening a PR, all three must pass. The pre-commit hook runs ruff
and mypy automatically on staged files.

## Code style

- **Formatter + linter: `ruff`.** Config in `pyproject.toml`. Don't fight
  it; if a rule is genuinely wrong for a piece of code, add a narrow
  `# noqa: CODE` with a one-line reason, or extend `per-file-ignores`.
- **Type hints everywhere.** Mypy is configured permissively for the core
  and strictly for `middleware/plugins/*`. Don't weaken plugin type hints.
- **Docstrings:** module-level and on public functions/classes. Short and
  factual — no marketing prose, no restating the signature.
- **No inline comments that restate the code.** Comments explain *why*,
  not *what*.
- **Logging over prints.** Use `logging.getLogger(__name__)`.
- **Errors:** raise `HTTPException` for request-scoped failures; log and
  continue for best-effort operations (see Fail-open rule above).

## What NOT to do

- Don't add dependencies casually — this runs in production. Justify each
  addition to `requirements.txt` in the PR description.
- Don't bypass `pre-commit` with `--no-verify`. If a hook is wrong, fix
  the hook.
- Don't commit `.env`, credentials, or anything from `config/`, `data/`,
  `minio-data/`, or `iqm-releases-mirror/`. They're gitignored; keep it
  that way.
- Don't introduce vendor or site names into the core. No
  `if vendor == "iqm":` anywhere in `middleware/` outside of `vendors/iqm/`.
- Don't add backward-compatibility shims for in-progress code. If a
  function belongs to a plugin, move its callers — don't add a
  re-export in the core to keep an old import path alive.

## Cutting a release

The project uses **CalVer** in the form `YYYY.MM.PATCH`. Full rules,
including pre-release labels and breaking-change signalling, live at
the top of [CHANGELOG.md](CHANGELOG.md). There is no fixed release
cadence; bump `YYYY.MM` to the current year/month when you cut one,
and `.PATCH` for fix-only follow-ups within the same month.

To cut a release:

1. Move entries from `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md)
   into a new dated section `## [YYYY.MM.PATCH] — YYYY-MM-DD`. Flag
   anything that requires operator action on upgrade (new env var,
   schema change, docker-compose change, etc.) with `**Breaking:**`.
2. Add a matching link reference at the bottom of the file.
3. Bump `version` in [pyproject.toml](pyproject.toml) to the same tag.
4. Commit (`chore: release <version>` or similar), then tag and push:
   ```bash
   git tag -a <version> -m "Release <version> — <one-line summary>"
   git push origin main
   git push origin <version>
   ```

Tags are bare (no `v` prefix) to match the existing history.

## Useful commands

```bash
# Run the whole stack locally
MIDDLEWARE_MODE=authentication docker compose up -d

# Lint / format / typecheck
ruff check .
ruff format .
mypy

# Run pre-commit over the whole tree (useful after big changes)
pre-commit run --all-files

# Rebuild just the middleware image
docker compose build fastapi-proxy reporter
```
