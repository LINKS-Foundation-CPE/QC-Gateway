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

## [2026.10.0] — 2026-10-02

### Added
- **Optional `S3_ENDPOINT_URL`: where the gateway's S3 client connects,
  separately from the public `MINIO_SERVER_URL` that links carry.** Inside a
  container the public address can be unreachable — a loopback address is the
  container itself — or a detour through the reverse proxy and its
  certificate. Unset, the client uses `MINIO_SERVER_URL` exactly as before.
- **Calibration runs in its own container, with its own token, and publishes
  a day-by-day index of the reports.** The machine's calibration endpoints
  (`/cocos/api/v4/calibration/runs` and each run's report) now refuse the
  device token: it authenticates, then gets 403 — for every path under
  `/cocos/api/v4`, not just those two — and only an administrator token is
  accepted. The job path keeps working with the device token, so calibration
  reports had stopped arriving while everything else looked healthy.

  Polling moves out of the job reporter into `middleware.calibration_poller`,
  a `calibration-poller` service built from the same image. It reads a new,
  optional `ADMIN_IQM_TOKEN` through the vendor plugin's new
  `build_calibration_headers()`; that token is held by this container alone
  and never reaches the reporter or the proxy, which carry user traffic.
  **Without it, calibration uses `IQM_SERVER_TOKEN` exactly as before**, so a
  deployment that sets nothing behaves as it did — and still works against a
  machine that has not tightened those endpoints. A credential the machine
  refuses is logged once, with the variable to set, and once more when it
  recovers — not as a stack trace every hour. A report the machine will not
  serve (the mock answers 500 for older runs) is reported once per run rather
  than on every poll.

  Each poll now also rewrites `calibration/index.json` and
  `calibration/index.html` in the bucket: every stored report, grouped by day
  in the deployment's `TZ`, newest first, with its size and a download link.
  Runs are dated by the machine's `end_time`, which is UTC — a report's
  `Last-Modified` header equals it to the second. Reports fetched before this
  change are dated from the machine's run listing on the first poll; one the
  machine no longer lists is placed by when it was fetched and marked
  approximate. Report paths are unchanged — job pages link to them by
  calibration set id — and `calibration_runs_processed` gains three nullable
  columns, added in place on first start.

  A failed poll is retried soon rather than after the full interval — first
  after a minute, doubling up to the interval — because the first poll after a
  deploy races the reverse proxy: `docker compose down && up` starts the poller
  while SWAG is still initialising, the object store's public URL has nothing
  listening on 443 yet, and at an hour's interval the first report waited an
  hour for nothing. The store is checked before any report is downloaded, so an
  unreachable store costs one line in the log, not several MB of downloads and
  a traceback per report. A refused credential still waits the full interval:
  that is configuration, and retrying every minute would not change it.

  To restore calibration on a machine that requires it, add
  `ADMIN_IQM_TOKEN` to the deployment's `.env`. Nothing else needs changing:
  the deploy's `docker compose up --remove-orphans` starts the new service.

- **Pluggable submission policy, with two backends.** Whether a submission may
  proceed — quota, budget, concurrency — is now a plugin point rather than a
  block of code in `proxy_and_capture`. `POLICY_PLUGIN` selects:

  - `passthrough` — the flat `MAX_CONCURRENT_SHOTS` / `MAX_CONCURRENT_SWEEPS`
    per-user limits, delegating to the same `ConcurrencyLimiter` as before,
    down to the wording of the 429s.
  - `shot_budget` — `min(n_licenses × SHOTS_PER_LICENSE, MAX_CONCURRENT_SHOTS)`
    concurrent active shots, with `n_licenses` taken from the cluster (for a
    `sqed` principal, the sum over live token fields the scheduler already
    reserved). Nothing is claimed from a pool: the scheduler is the only
    allocator, so a principal that did not come from one falls back to the
    flat cap. Sweeps are exempt and keep `MAX_CONCURRENT_SWEEPS`.

  **Not breaking.** The default is `passthrough`, which is the behaviour that
  preceded the plugin point; `tests/test_policy_loader.py` and the parity
  tests in `tests/test_policy_passthrough.py` pin that, including the refusal
  messages.

  The protocol has three calls, not one, because capacity is reserved *before*
  the request is proxied and the machine may still refuse it:
  `check_submission` reserves, `on_submission_accepted` commits once a job id
  exists, `rollback` returns the capacity. `PolicyDecision.reservation` is an
  opaque bag the plugin fills and the core hands back, so per-submission state
  does not live on the plugin instance — two submissions are in flight
  whenever two clients are.

  `ConcurrencyLimiter.try_reserve` gains an optional `max_shots_override`, the
  hook a budget-computing plugin needs to substitute a per-principal ceiling
  for the flat one. Unset, it behaves exactly as before.

- **Pluggable authentication, with two backends.** Which credential the gateway
  accepts is now a deployment choice rather than a fact about the code. The
  `AuthPlugin` protocol turns a bearer token into a `Principal`, and
  `AUTH_PLUGINS` names an ordered chain:

  - `keycloak` — RS256 JWTs against the realm's JWKS, roles from
    `realm_access.roles`. Exactly what `authentication.py` did before, moved
    into a plugin.
  - `sqed` — per-job tokens minted by a SLURM SPANK plugin onto a Redis bus,
    for a deployment where submissions come from the scheduler and not from a
    person. Reads `qpu:token:<token>` as a hash with one field per live job,
    so a job array sharing one token behaves correctly as its jobs start and
    finish; carries the SLURM `account` and license count in
    `Principal.metadata`.

  **Not breaking.** The default is `keycloak` alone, and it claims no token
  prefix, so a JWT reaches it through the fallback chain exactly as before. A
  deployment that sets nothing sees no change; `tests/test_auth_loader.py`
  pins that.

  Routing is what keeps the chain additive: a token carrying a plugin's prefix
  goes to that plugin alone — a token that announces its issuer and then fails
  is a failure, not a reason to keep guessing — while a prefix-less token is
  offered to the prefix-less plugins in order. Adding a prefixed plugin cannot
  shadow an existing credential.

  `RoleAuthorizationChecker.check` now takes a `Principal` instead of the old
  `User`; it read the field defensively already, so no RBAC behaviour changed.

- **A test suite.** This repository had none. 32 tests covering the auth chain's
  routing and failure semantics, both shipped plugins, and the default-chain
  guarantee above. `pytest`, `pytest-asyncio` and `fakeredis` are added to
  `requirements-dev.txt`, and `pyproject.toml` gains the pytest configuration.

### Fixed
- **The S3 client's own endpoint is `S3_ENDPOINT_URL`, not `MINIO_INTERNAL_URL`.**
  Early deployments carry `MINIO_INTERNAL_URL=minio-job-data` in their `.env`,
  read by nothing for a long time; when the variable was given a meaning,
  production's uploads went to `http://minio-job-data:80` — a container the
  RustFS move had removed — and every job submission failed with 500 while the
  jobs themselves ran. The new name has no history, a value without a scheme is
  ignored with a warning, and `MINIO_INTERNAL_URL` is read by nothing again. An
  old line in `.env` is harmless and can be deleted.
- **The job portal renders results again on RustFS.** The portal (`jobs.*`)
  fetches every file from `store.*`, a different origin. MinIO answered with
  CORS headers for any origin by default; RustFS sends none until the bucket
  has CORS rules, so after the cutover the browser blocked every fetch and the
  pages came up empty, sweeps included. `rustfs-init` now sets a rule allowing
  `GET`/`HEAD` from any origin — no more than anonymous read-by-key already
  allows. Takes effect on the next `up`, or at once with
  `docker compose run --rm rustfs-init`.
- **The object store no longer lets anyone list the bucket.** `minio-init`
  applied `mc anonymous set download`, and that canned policy grants
  `s3:ListBucket` alongside `s3:GetObject`. The whole job-data bucket could be
  listed anonymously — confirmed on both deployments — which exposed every
  user's e-mail address and job id, and from there every circuit and result,
  since objects are readable by key.

  The bootstrap now sets an explicit policy granting anonymous `s3:GetObject`
  on the bucket's objects and nothing else. `mc anonymous set-json` replaces
  the whole bucket policy, so the next deploy also removes the grant a previous
  run left behind — no manual step, though applying the same policy by hand
  closes it sooner. Nothing depends on anonymous listing: the job portal, the
  dashboard and the calibration index all fetch objects by known key.

- **The pulse schedule shows the pulses.** Three faults, all of which the
  playlist card had been hiding behind plausible-looking output.

  *The detail panel was always empty.* It zoomed to a fixed window at the
  origin, which is where the synthetic fixtures put their pulses. A real
  schedule opens with the reset delay — 300 µs of `wait` on every lane before
  a 2 µs burst — so the window showed the empty part every time. It is now
  computed from the first and last instruction that is not a `wait`, and the
  full-schedule caption says how much reset delay it skipped.

  *The readout pulse was never drawn.* A `readout_trigger` names its probe
  through `probe_pulse_ref`, which indexes the channel's **instruction**
  table, not its waveform table — usually pointing at a `multiplexed_iq_pulse`
  whose entries point at the `iq_pulse` that finally names a waveform. Read as
  a waveform index it was simply wrong: on a channel with fewer waveforms than
  instructions the readout drew nothing, and on one with more it drew some
  other pulse's shape. The reference is now followed to the waveform actually
  played, and kept as `probe` alongside it. A trigger also holds the
  acquisition window open longer than the probe it plays, so the envelope is
  drawn for the waveform's own length rather than the trigger's.

  *Lanes were laid out in samples.* Sample counts from two channels running at
  different rates are not comparable, and putting them on one axis as if they
  were would stretch one lane against another. Every position on the chart is
  now in nanoseconds, converted per lane from that lane's own sample rate.

- **Sweep jobs show their pulse schedule again — it was never being read.**
  `parse_run_definition` decoded the payload as a v1 `RunDefinition`, which
  has twelve fields. The machine sends a v2 one, whose thirteenth field,
  `sweep_definition_payload`, is a `google.protobuf.Any` wrapping the whole
  `SweepRequest` — and that is where the playlist actually is. Field 12,
  `sweep_definition`, is left unset. A field a message definition does not
  know about is skipped rather than reported, so the parser read the axes and
  the readout metadata correctly and silently dropped 800 kB of schedule.

  This is the same failure as the sweep *results* had, for the same reason: a
  newer message read with an older definition does not fail, it goes quiet.
  Checked against both payloads available — one from the production machine
  (6 channels, 250 schedules, 255 acquisitions) and one from the mock — and
  both carry the schedule in field 13.

  Only the sweep definition is read through v2. v2 also retypes
  `additional_run_properties` from `google.protobuf.Struct` to its own
  `Struct`, whose `MessageToDict` yields `{"stringValue": ...}` where v1 gives
  a plain string; reading the whole message through v2 would change the shape
  of every readout-metadata field in the sidecar. The regression test asserts
  both halves.

  Existing sidecars are not rewritten. A job has to be re-run, or its
  `payload.bin` re-parsed, for the schedule to appear.

- **The documentation describes the gateway that is actually running.** The
  pluggable authentication and policy work above added its own sections to
  `docs/DOCUMENTATION.md` and `docs/MODULARIZATION.md`, but left the
  surrounding prose describing the gateway as it was before: the request-flow
  steps still said authentication was `get_current_user` validating a JWT and
  step 9 was `ConcurrencyLimiter`, the module list did not mention
  `middleware/auth/` or `middleware/policy/`, the `datatypes` list omitted
  `Principal` and `PolicyDecision`, and none of `AUTH_PLUGINS`,
  `STRICT_PREFIX_MODE`, `POLICY_PLUGIN`, the `SQED_*` settings or
  `SHOTS_PER_LICENSE` appeared under Configuration. The sweep work was missing
  too: no `vendors/iqm/sweep_parser.py`, no `jobs-portal/`, no mention of the
  parsed sidecars written beside each binary artifact, and `parse_artifact()`
  absent from the vendor-plugin checklist.

  Also adds the two extension guides that were missing next to the existing
  vendor and site ones — how to add an auth plugin and a policy plugin,
  including the two rules that are easy to get wrong: returning `None` versus
  raising `AuthError`, and keeping per-submission state in the decision's
  `reservation` bag rather than on the plugin instance.

  Documentation only; no code changes.

- **A current qiskit-iqm can reach the machine again.** From 15.x the client
  fetches the dynamic quantum architecture, `GET
  /api/v1/calibration/default/gates`, while constructing a backend — before it
  submits anything; older clients paired with this generation of the machine
  API fetch the static `GET /api/v1/quantum-architecture` instead. Neither path
  was in the IQM route table, and the middleware answers an unconfigured path
  with 403 *"path not allowed"* before routing, so the client failed at
  construction with no job ever attempted. Both are now gated on `cortex_user`,
  the same role as the calibration set they sit beside: read-only metadata, no
  wider than what a user could already read.

- **The job reporter no longer goes silent after a backwards clock
  correction.** Its cycle was timed with `time.time()`. An NTP step backwards
  between the start of a pass and the end of it makes the measured cycle
  negative, and the remaining sleep then grows by the whole size of the step —
  the reporter sleeps it out one second at a time, logging nothing, and every
  job it has not yet finalised stays unfinalised until it wakes. Seen on a VM
  corrected shortly after boot: two cycles, then hours of silence. Timing is
  now on `time.monotonic()`, which no correction can move, and the wait is a
  named function (`_wait_for_next_cycle`) with tests. The calibration-poll
  deadline moves to the same clock, and starts at `None` rather than `0` — on
  a monotonic clock zero is roughly process start, which would have read as
  *just polled* and held the first poll back by a full interval.

- **The site-plugin authorization call no longer inherits the machine request's
  body framing.** `proxy_and_capture` copied every header of the incoming
  submission except `host` and `content-length` onto the call the site plugin
  makes to the portal — a different request, with a body the gateway builds
  itself. `content-type` came along with the rest, and httpx only *defaults* its
  own content headers (`setdefault`), so the forwarded one won.

  A sweep is submitted as a byte blob, so `/api/v1/jobs/default/run` carries a
  non-JSON content-type, and the portal's `/jobAuthorizer` therefore declined to
  parse the JSON the gateway sent it. An empty body reads as a request that
  claimed no project and no job type, so the portal authorized it against the
  user's default project: pulse access was not enforced on the one path that
  needs it. Circuits are submitted as JSON, so their content-type was harmless
  and the fault showed only on sweeps.

  Entity and framing headers are now dropped from the forwarded set
  (`UNFORWARDABLE_REQUEST_HEADERS`); identity headers, `Authorization` above all,
  still pass through. The payload is also logged at DEBUG, so what the portal was
  asked can be read rather than inferred.

### Removed
- **Breaking (operator action): MinIO is retired.** The `minio` service is
  gone from both compose files, `rustfs-init` no longer configures migration
  from it, and `restic` no longer backs up `${MINIO_STORAGE_PATH}/data`. Deploy
  this only after `docker compose logs rustfs-init` on the previous release
  ends in `Backfill completed, nothing failed: MinIO can be retired.`

  On its first run `rustfs-init` removes RustFS's pointer at MinIO — refusing,
  with a non-zero exit and the reason, if the backfill is not complete, since
  whatever it has not copied exists only in MinIO's directory. It mounts
  `${MINIO_STORAGE_PATH}` read-only to check what is left there: after a
  finished migration it reports `${MINIO_STORAGE_PATH}/data` as safe to delete
  (the operator deletes it; nothing here does), and a MinIO bucket that was
  never migrated is an error, so a deployment that skips the migration release
  finds out instead of silently losing its results. Check
  `docker compose ps -a rustfs-init` for `Exited (0)`: nothing waits on it, so
  the deploy itself does not fail. Restic snapshots taken before this change
  still hold MinIO's copy and expire under the existing `--keep-last 7`.

### Changed
- **Breaking (operator action): the object store is RustFS; MinIO stays only
  as the source it migrates from.** MinIO is no longer distributed — the
  `minio/minio` and `minio/mc` images are gone from Docker Hub, `mc` from
  everywhere, and a fresh deploy could no longer pull `minio-init` at all.
  `rustfs` (RustFS 1.0.0, Apache-2.0, pinned by digest) takes over MinIO's
  published ports `127.0.0.1:9000`/`:9001` and its `MINIO_ROOT_*` credentials,
  so SWAG's `store.*` vhost, `MINIO_SERVER_URL`, the uploader and every link
  already handed out are unchanged. `minio-init` is replaced by `rustfs-init`,
  built from `rustfs-init/` around the RustFS CLI pinned by version and
  checksum; it sets the same GetObject-only anonymous policy (the bucket stays
  unlistable).

  MinIO keeps running on its old data with no published ports, on the image
  tag it always had — the copy each deployment already holds locally, since no
  registry serves that release any more. `rustfs-init` points RustFS's
  on-demand migration at it: a key RustFS does not have yet is served from
  MinIO and copied in on first read, same bytes and ETag, and a background
  backfill copies the rest. Uploads from the cutover on land in RustFS only.
  `restic` now also backs up `${MINIO_STORAGE_PATH}/rustfs`. Dev compose
  follows the same layout.

  **Before deploying:** RustFS's copy is a second full copy on the same disk
  until MinIO is retired, so check `du -sh ${MINIO_STORAGE_PATH}/data` against
  free space on that disk. **After deploying:** `docker compose logs
  rustfs-init` should end in `Backfill completed, nothing failed: MinIO can be
  retired.`; `docker compose run --rm rustfs-init` re-checks and restarts a
  backfill that had failures. Retiring MinIO is a follow-up change, made only
  after that line. **Rolling back** means copying back what was uploaded after
  the cutover (`rc mirror --newer-than <age of the cutover, e.g. 3d>` from
  RustFS to MinIO) before reverting this change; otherwise those results are
  lost to the links that point at them.

- **Breaking (operator action): the sweep route asks only for `cortex_user`.**
  `/api/v1/jobs/default/run` no longer requires the `pulla_user` realm role in
  the token; pulse access is decided by the site plugin's authorization call
  instead, from the platform's own record of the grant. That is what lets an
  administrator grant or revoke it, and what covers principals whose tokens carry
  no roles at all.

  **Deploy the backend first.** A portal that predates the `job_type` field in
  the authorization payload does not check pulse access, so a gateway relaxed
  ahead of it leaves the sweep path open to any `cortex_user`. Note also that the
  site call is made only in `MIDDLEWARE_MODE=production` and only for the logged
  routes: `maintenance` answers every request with 503 before routing, so it is
  not a concern, but in `authentication` and `reporting` the request proceeds and
  nothing gates the sweep path beyond `cortex_user`.
- The site plugin's `authorize_job` receives **`job_type`** — the vendor
  plugin's classification of the request, `circuit` or `sweep` for IQM — and the
  SPARK site plugin forwards it to `/jobAuthorizer`. The call previously carried
  `{username, project_name}` and nothing else, so a site had no way to make the
  decision depend on *what* was being submitted; the sweep path is privileged and
  the backend can now say so from its own record of the grant rather than relying
  solely on a realm role in the token. Added as a defaulted parameter, so a site
  plugin written before this still satisfies the Protocol, and only sent when the
  vendor plugin classified the request, so a portal that predates the field
  ignores it.
- **Breaking:** the deployment configuration under `config/` is now
  deployment-agnostic. The nginx vhost templates are named after the service
  they front (`frontend`, `api`, `dashboard`, `store`, `jobs`, `grafana`,
  `prometheus`, `status`, `rng`, `docs`) instead of after one deployment's
  domains; every host name already came from `.env`, so only the file names and
  `configure_nginx.sh` change, and the rendered vhost file names are unchanged.
  Two values that were hardcoded are now configuration:
  - `DOCS_URL` / `INTERNAL_DOCS_URL` — the targets of the `docs.$BASE_DOMAIN`
    redirect vhost, which previously pointed at one specific documentation
    site. The vhost is rendered only when `DOCS_URL` is set.
  - `FAIL2BAN_IGNOREIP_EXTRA` — trusted address ranges appended to fail2ban's
    `ignoreip`, which previously carried one deployment's public range.
    `config/fail2ban/jail.local` is therefore now generated from
    `jail.local.template` by `configure_nginx.sh`, like the vhosts.

  **Operator action:** add `DOCS_URL` (and `INTERNAL_DOCS_URL` if the `/private`
  redirect is used) and `FAIL2BAN_IGNOREIP_EXTRA` to `.env` before deploying,
  or the docs vhost disappears and the deployment's own range is no longer
  exempt from fail2ban. `FAIL2BAN_IGNOREIP_EXTRA` must be quoted if it lists
  more than one range.
- `S3Uploader` no longer falls back to a hardcoded object-store URL when
  `MINIO_SERVER_URL` is unset; it raises. The value is deployment-specific and
  is what artifact links point at, so a silent default failed later and less
  clearly. `MINIO_SERVER_URL` was already required by `middleware/config.py`.

### Fixed
- **The results page showed the wrong readout fidelities.** A calibration set
  carries two SSRO tasks per qubit — `metrics.ssro.measure.constant.*`, the
  readout calibration of the `measure` gate, and
  `metrics.ssro.measure_fidelity.constant.*`, the separate fidelity
  characterisation — and they report different numbers for the same qubit. One
  regex matched both and both were stored under the qubit name, so whichever
  observation happened to come last in the artifact silently replaced the other,
  and the artifact does not define an observation order. On the machine's own
  data this made four of five qubits read 97.x% where the calibration set has
  three of five above 98%, disagreeing with the `gate="measure"` panels in the
  Perses dashboards over the same values. The page now names the task it renders
  — `measure`, the canonical readout fidelity — instead of letting file order
  decide, and falls back to whatever other SSRO task an artifact carries,
  labelled in the header so it cannot be read as the canonical one. The PRX
  section is keyed by implementation against the same class of collision
  (`drag_gaussian` and `drag_crf`), matching how the CZ sections were already
  grouped.
- Calibration tiles are ordered by qubit number rather than lexically, which
  put `QB10` before `QB2` on any machine with ten or more qubits.
- `.dockerignore` added: the middleware image build used `COPY . .` with no
  context exclusions, so runtime data accumulating in the deploy directory
  (`minio-data/`, `iqm-releases-mirror/`, SWAG `config/`, …) was shipped
  into the build context and baked into the images. On the deploy host this
  had grown the context to ~800 MB, inflating deploy times from ~80 s to
  45+ min and embedding MinIO job artifacts in the images.

### Added
- **`overview.$BASE_DOMAIN` vhost**, fronting a service the deployment runs
  beside the gateway in the same way grafana is fronted. The upstream comes
  from **`OVERVIEW_UPSTREAM`** (a full URL — with SWAG on the host network, a
  local service is `http://127.0.0.1:<port>`), and the vhost is rendered only
  when that is set, like the docs one. **Operator action:** set
  `OVERVIEW_UPSTREAM`, and add `overview.<domain>` to `EXTRA_DOMAINS` so the
  certificate covers the new host — otherwise it answers with a name mismatch.
- `docker-compose.dev.yaml` + `env.dev.example` + `config-dev/nginx-tls.conf`:
  standalone development stack that runs without SWAG, CA-issued certificates,
  or a FQDN — every published port (gateway :8000, MinIO :9000/:8901, jobs
  portal :8940, optional dashboard dev server :8080 via the `dashboard`
  profile) speaks HTTPS behind a `tls-proxy` nginx using a self-signed cert
  shared with the dev Keycloak (keycloak-deployment repo). Documented in
  `docs/DEV_DEPLOYMENT.md`.
- `JOBS_PORTAL_URL` (SPARK site setting, optional): full-URL override for
  the jobs results portal used in `build_results_url`; defaults to the
  existing `https://jobs.${BASE_DOMAIN}` behaviour when unset.
- `LICENSE` file: the project is now distributed under the European
  Union Public Licence v. 1.2 (EUPL-1.2). See the Licensing section of
  `README.md` for the rationale behind the choice and what it means
  in practice for users, operators, and plugin authors.

### Changed
- `pyproject.toml`: migrated the `license` field from the legacy
  `{ file = "LICENSE" }` form to the PEP 639 SPDX expression
  `license = "EUPL-1.2"` plus `license-files = ["LICENSE"]`.

### Fixed
- `/health` added to the IQM `DEFAULT_PUBLIC_ROUTES`: the endpoint was
  defined in `middleware/main.py` but unreachable — the proxy middleware
  whitelist returned 403 before the route was ever hit, breaking external
  health checks.

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

[Unreleased]: https://github.com/LINKS-Foundation-CPE/QC-Gateway/compare/2026.10.0...HEAD
[2026.10.0]: https://github.com/LINKS-Foundation-CPE/QC-Gateway/releases/tag/2026.10.0
[2026.04.0]: https://github.com/LINKS-Foundation-CPE/QC-Gateway/releases/tag/2026.04.0
