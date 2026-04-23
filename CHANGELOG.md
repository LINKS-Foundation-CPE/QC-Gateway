# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses **CalVer** in the form `YYYY.MM.PATCH`:

- `YYYY.MM` is bumped when a release is cut in that year and month. There is
  no obligation to release every month; the date simply records when the
  release happened.
- `.PATCH` is bumped for fix-only follow-up releases within the same month,
  starting at `.0`.
- Pre-release labels (e.g. `-rc1`, `-pre-<name>`) may be appended when
  appropriate.

Entries marked **Breaking:** require operator action on upgrade — typically
a change to `.env`, `docker-compose.yaml`, the database schema, or the
on-disk layout of the deploy directory.

## [Unreleased]

### Added
- `LICENSE` file: the project is now distributed under the European
  Union Public Licence v. 1.2 (EUPL-1.2). See the Licensing section of
  `README.md` for the rationale behind the choice and what it means
  in practice for users, operators, and plugin authors.

### Changed
- `pyproject.toml`: migrated the `license` field from the legacy
  `{ file = "LICENSE" }` form to the PEP 639 SPDX expression
  `license = "EUPL-1.2"` plus `license-files = ["LICENSE"]`.

## [2026.04.0] — 2026-04-17

First versioned release. This baseline captures the state of the project at
the time open-sourcing preparation was completed: a vendor- and
site-agnostic core with reference plugins for IQM and SPARK, developer
tooling, and a documented contribution flow. Changes accumulated prior to
this tag are captured here as a single inaugural entry; future releases
will track individual changes.

### Added
- Plugin architecture with `VendorPlugin` and `SitePlugin` `Protocol`
  contracts and shared dataclasses at the plugin boundary. Reference
  implementations: `iqm` (vendor) and `spark` (site).
- Developer tooling: `ruff` (lint + format), `mypy` (strict on
  `middleware.plugins.*`, permissive elsewhere), and `pre-commit` wired
  up via `pyproject.toml` and `.pre-commit-config.yaml`.
- Public-facing documentation: rewritten `README.md`, `CONTRIBUTING.md`,
  `CLAUDE.md`, and a "Transparent to existing SDKs" callout explaining
  that the gateway preserves the upstream vendor API verbatim so client
  SDKs work without modification.
- Logging improvements: `basicConfig(force=True)` at module import,
  WARNING-level startup banner, and an `Auth failed` WARNING for every
  rejected authentication attempt.
- CI/CD deploy: `rsync --delete` with a curated exclude list and a
  per-deploy timestamped backup snapshot (`--backup-dir`) to make
  unintended deletions trivially recoverable.

### Changed
- **Breaking:** environment variables `SPARK_URL` → `FRONTEND_URL` and
  `API_URL` → `JOB_PORTAL_API_URL`. Deployed `.env` files must be
  updated in lockstep with the code.
- **Breaking:** `jobs-portal/` moved to
  `middleware/vendors/iqm/jobs-portal/`; `docker-compose.yaml` bind
  mount updated accordingly. The container-internal mount target
  (`/jobs-portal`) is unchanged, so the nginx vhost template is
  untouched.
- Calibration polling cadence is owned by the vendor plugin
  (`get_calibration_poll_interval()` on the `VendorPlugin` Protocol),
  no longer imported from `IQMSettings` by the core background worker.
- JWT authentication switched from `python-jose` (unmaintained, two
  unpatched CVEs from April 2024) to `PyJWT`. No change to the external
  authentication contract (issuer, audience, RS256).

### Removed
- Deprecated backward-compatibility re-exports in `middleware.utils`,
  `middleware.job_capture`, `middleware.calibration`, and
  `middleware.authorization`. Callers have moved to the vendor and site
  plugin modules.

[Unreleased]: https://gitlab.linksfoundation.com/links-iqm-spark/machine-management/nginx-reverse-proxy/-/compare/2026.04.0...HEAD
[2026.04.0]: https://gitlab.linksfoundation.com/links-iqm-spark/machine-management/nginx-reverse-proxy/-/tags/2026.04.0
