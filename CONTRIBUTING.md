# Contributing to QC Gateway

Thanks for your interest! This document covers how to get a development
environment running, the coding standards we enforce, and the pull-request
workflow. If you are an AI assistant working on the codebase, also read
[CLAUDE.md](CLAUDE.md) — it covers architectural invariants that are not
repeated here.

## Ways to contribute

- **New vendor plugins** — wire a different quantum backend behind the
  `VendorPlugin` protocol.
- **New site plugins** — adapt the gateway to a different
  authorization/reporting target (SLURM accounting, a different billing
  portal, etc.).
- **Core improvements** — generic hardening, observability, tests,
  documentation.
- **Bug reports and usability feedback** — open an issue with clear
  reproduction steps.

Please skim [DOCUMENTATION.md](docs/DOCUMENTATION.md) and
[MODULARIZATION.md](docs/MODULARIZATION.md) before proposing architectural
changes.

## Development environment

Requirements:

- Python 3.11+
- Docker + Docker Compose (for running the full stack)
- `git` and `pre-commit`

Set up a virtualenv and install the dev toolchain:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pre-commit install
```

`requirements-dev.txt` includes the runtime dependencies plus `ruff`,
`mypy`, and `pre-commit`.

## Coding rules

All of these are enforced by tooling — read the configs in
`pyproject.toml` and `.pre-commit-config.yaml` for the authoritative
source.

### Formatting and linting — `ruff`

We use a single tool for formatting, import sorting, and linting.

```bash
ruff format .          # auto-format
ruff check .           # lint
ruff check --fix .     # lint with safe auto-fixes
```

Selected rule sets: `E`, `W`, `F`, `I`, `B`, `C4`, `UP`, `SIM`, `RUF`,
`N`, and a curated subset of `PL`. Line length: 100.

If a rule genuinely does not apply to a specific line, use a narrow
`# noqa: <CODE>` with a comment explaining why, rather than disabling the
rule globally.

### Type checking — `mypy`

```bash
mypy
```

Config lives in `pyproject.toml`. The core is configured permissively so
the codebase can be onboarded incrementally; `middleware/plugins/*` is
configured strictly (`disallow_untyped_defs`, `disallow_incomplete_defs`)
because the plugin contracts are load-bearing.

**Rule of thumb:** add type hints to everything you touch. Do not weaken
existing hints to make mypy quiet — fix the type.

### Pre-commit

`pre-commit install` wires ruff + mypy + a handful of hygiene checks
(trailing whitespace, EOL, YAML/TOML validity, large files, private keys)
into your commit flow.

Run them over the entire tree after large changes:

```bash
pre-commit run --all-files
```

Do **not** bypass hooks with `git commit --no-verify`. If a hook is
incorrect, fix the hook.

### Style conventions

These go beyond what the linter can enforce:

- **Module docstrings** on every `.py` file that contains public API.
  One short paragraph is enough.
- **Public functions and classes** get a one-line docstring at minimum.
  No marketing prose; no restatement of the signature.
- **Comments explain `why`, not `what`.** If a comment only restates the
  code, delete it.
- **Logging, not `print`.** Use `logging.getLogger(__name__)` at module
  scope.
- **No wildcard imports.** No relative imports outside of `__init__.py`.
- **Errors:** raise `HTTPException` for request-scoped failures; log and
  continue for best-effort operations (Redis/MinIO/portal non-essential
  paths). See the Fail-open rule in [CLAUDE.md](CLAUDE.md).
- **No vendor or site names in the core.** `middleware/` outside
  `vendors/` and `sites/` must not branch on vendor/site identity.
- **No new backward-compat re-exports.** The existing ones in
  `middleware/utils.py`, `middleware/job_capture.py`, and
  `middleware/calibration.py` are scheduled for removal.

## Running the application locally

For quick iteration without Docker, run the two processes directly:

```bash
cp env.example .env     # fill in required values
export $(grep -v '^#' .env | xargs)
uvicorn middleware.main:app --reload --host 0.0.0.0 --port 8000
python -m middleware.job_reporter   # background worker
```

For an end-to-end environment, use Docker Compose (starts PostgreSQL,
Redis, MinIO, nginx/certbot, and the two middleware processes):

```bash
MIDDLEWARE_MODE=authentication docker compose up -d
```

## Tests

Automated tests are not yet published in-tree. If you add test
infrastructure (`pytest` recommended), please:

- Put tests under `tests/` mirroring the source layout.
- Mock external services (upstream vendor, Redis, MinIO, PostgreSQL) by
  default; add opt-in integration tests guarded by env vars for the real
  services.
- Wire the suite into `pre-commit` and/or CI as appropriate.

## Pull request workflow

1. **Fork and branch.** Use a descriptive branch name
   (`feat/ibm-vendor-plugin`, `fix/concurrency-rollback`).
2. **Keep the change focused.** One logical change per PR. Separate
   refactors from behavior changes.
3. **Make the commit message explain the `why`.**
4. **Run all checks locally:**
   ```bash
   pre-commit run --all-files
   mypy
   ```
5. **Update documentation** in the same PR:
   - Core request-flow changes → update [DOCUMENTATION.md](docs/DOCUMENTATION.md) §Flow.
   - Plugin interface changes → update [MODULARIZATION.md](docs/MODULARIZATION.md).
   - New env vars → update `env.example`.
   - User-visible changes → update [README.md](README.md).
6. **Open the PR against `main`** with a description that covers:
   - What changed and why.
   - Any new configuration / env vars.
   - How you tested it.
   - Any follow-up items that are intentionally out of scope.

## Security

Please **do not** open public issues for security reports. Email the
maintainers privately (see repository metadata) with a description and
reproduction. We'll acknowledge within a reasonable window and coordinate
a fix and disclosure.

## License

By contributing, you agree that your contributions will be licensed
under the [European Union Public Licence v. 1.2 (EUPL-1.2)](LICENSE)
that covers the project. See the Licensing section of
[README.md](README.md) for what this means in practice for users,
operators, and plugin authors.
