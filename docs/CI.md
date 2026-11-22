# Vibepollo patch pipeline: CI/release automation

Scope: `emilwojcik93/Vibepollo` (public fork). Implements PLAN sections 4.2, 4.3, 4.7, 5.2, 5.4, 5.5, 9.3
and the producer side of `CONTRACT.md` (v1). Everything here lives on the `automation` branch (default
branch of the fork); build branches `build/<tag>-n<N>` are generated, never edited by hand.

## 1. Flow

```
cron (hourly 07-11 UTC + 6-hourly)            manual: workflow_dispatch (tag, dry_run, override_hold, force_rebuild)
        |
  upstream-watch / resolve   (contents:read)
        |  list Nonary/Vibepollo releases -> numeric, published, non-draft -> newest
        |  deref annotated tag -> commit SHA; compare with upstream-seen.json (moved tag = HALT + alert)
        |  fingerprint patches/series; plan: build | rerun | noop | hold | halt
        v
  upstream-watch / prepare   (environment build-push; GITHUB_TOKEN contents:write for upstream-seen.json only)
        |  fetch tag (verify SHA again) -> git am --3way series (pinned identity/dates, reproducible)
        |  guard: patches may not touch gitlinks, .gitmodules, .github/, VHF contract pins
        |  strip every upstream workflow except ci-windows.yml; add wrapper + .vp-ci/ + build-meta.json
        |  push build/<tag>-nN with the GitHub App token  ----> push event starts the wrapper
        v
  patched-windows-release.yml  (on the build branch)
     validate  (contents:read)   regex-check ref + metadata, recompute versions, ancestry check
     build     (actions:read, contents:read)   uses ./.github/workflows/ci-windows.yml, secret-free inputs, tests ON
     package   (no permissions)  msiinfo: MSI ProductVersion window gate, custom-action table, preview manifest
     publish   (contents/attestations/id-token:write; dry_run=false only)
               stage -> manifest.json + SHA256SUMS -> actions/attest -> self-verify like a consumer
               -> DRAFT release + upload -> digest check -> publish -> gh release verify
     notify    (actions:write)   dispatches notify.yml on automation (issue + GChat)
```

### Why the build starts from a push, not from `workflow_dispatch`
A workflow_dispatch needs the workflow file to be registered on the default branch; `patched-windows-release.yml`
exists only on build branches (it must live in the tree that `ci-windows.yml` checks out). Pushing the branch with an
App token (App tokens, unlike `GITHUB_TOKEN`, trigger workflows) starts the run bound to the exact pushed commit.
`workflow_dispatch` stays in the wrapper only for manual re-runs on an already-registered workflow. Retries of failed
builds use `gh run rerun --failed` (planned by `plan_build.py`, at most one automatic retry, then `stuck` + alert).

## 2. Files (branch `automation`, owned by the CI/Release Engineer)

| Path | Purpose |
|---|---|
| `.github/workflows/upstream-watch.yml` | poll, plan, create + push `build/*` branch, record `upstream-seen.json`, re-run failed builds |
| `ci/wrapper/patched-windows-release.yml` | TEMPLATE copied into each build branch as `.github/workflows/patched-windows-release.yml` |
| `.github/workflows/notify.yml` | one alert issue (label `automation-alert`) + sanitized GChat post |
| `.github/workflows/rot-matrix.yml` | apply the series to the last 5 upstream releases (weekly, and on patch changes) |
| `.github/workflows/keepalive.yml` | weekly re-enable of scheduled workflows + dead-man check (3 days) |
| `.github/workflows/ci-selftest.yml` | pytest + actionlint + zizmor on every change to `ci/` and `.github/` |
| `.github/dependabot.yml` | weekly updates of SHA-pinned actions |
| `ci/*.py` | helpers (all stdlib, python3 >= 3.8), copied to `.vp-ci/` in build branches |
| `ci/tests/` | pytest suite (malicious tag fixtures in `ci/tests/fixtures/releases.json`) |
| `ci/rot-known-fail.txt` | upstream tags the series is known not to apply to (explicit, short) |

Helpers: `vpcommon` (regexes, safe `$GITHUB_OUTPUT` writer), `validate_tag`, `select_release`, `deref_commit`,
`version_map`, `series` (series parser + fingerprint), `policy` (strict `policy.yml` reader), `lock_update`,
`plan_build`, `apply_series`, `assemble_build`, `sensitive_paths`, `wrapper_params`, `stage_assets`, `msi_summary`,
`manifest_gen` (manifest + SHA256SUMS), `notify_text`, `rot_matrix`.

### Interfaces expected from the Patch Engineer (`automation` branch)
* `patches/series`: one patch path per line relative to `patches/`, `#` comments; optional `# group: NAME` lines
  assign groups (default group `posture`). `series_sha256` covers the series file and every patch body (see `ci/series.py`).
* `policy.yml` exactly as CONTRACT section 5 (parsed by `ci/policy.py`; unknown shapes fail closed).
* `upstream-seen.json` (renamed from `upstream.lock`, which the Patch Engineer uses for the reference pin of the series): JSON schema 1 written by `ci/lock_update.py` (documented in that file). The first commit recorded
  for a tag is authoritative; an empty/absent file is valid.
* `tools/apply-series.sh` / `tools/rot-matrix.sh` are NOT used by CI; `ci/apply_series.py` and `ci/rot_matrix.py`
  implement the same steps with pinned identity/dates so the build tip SHA is reproducible and with a conflict report.

## 3. Versioning (ADR-3, PLAN 5.5)

| item | value for upstream `X.Y.Z`, rebuild `N` |
|---|---|
| release tag | `X.Y.Z-vp.N` |
| build branch | `build/X.Y.Z-nN` |
| `release_version` | `X.Y.(Z+1)-alpha.N` (N 1..29), `-beta.(N-29)` (30..58), `-rc.(N-58)` (59..87) |
| MSI ProductVersion | `X.Y.(100*(Z+1)+ORD).0`, ORD = N / 30+k / 60+k |

Example: `2.20.0`, N=1 -> `2.20.1-alpha.1` -> `2.20.101.0`, strictly between upstream `2.20.99.0` and the next upstream
`2.20.199.0`. Limits: X,Y <= 255, Z+1 <= 654, N <= 87.

**Mandatory release gate** (live incident: `2.20.0-posture.1` -> `2.20.90.0` < `2.20.99.0` destroyed an install):
`package` job reads the built MSI with `msiinfo export <msi> Property` and fails unless
`upstream(X.Y.Z) < ProductVersion == build.msi_product_version < upstream(X.Y.(Z+1))`. The computed values are written to
`manifest.json` (`build.version`, `build.msi_product_version`). `wrapper_params.py` additionally refuses any build whose
metadata disagrees with the version mapper. Unit tests cover all 87 values of N against the neighbouring upstream versions.

## 4. Security model

* Top-level `permissions: {}` everywhere; per-job minimum (see flow). The build job and the unprivileged `package` job
  have no write scope, no `id-token`, no secrets. Only `publish` has `contents/attestations/id-token: write`.
* Every third-party action pinned by full SHA with a version comment (`test_workflows.py` enforces; Dependabot updates
  `.github/workflows`; the wrapper template pins are kept in sync by the same test).
  Pins in use: checkout v7.0.1, upload-artifact v7.0.2, download-artifact v8.0.2, attest v4.2.2, create-github-app-token v3.3.0.
* Untrusted strings (tags, branch names, release ids, inputs) are regex-validated (`vpcommon`), passed through `env:`
  and never interpolated into `run:` (a test scans all workflows for `${{` inside `run:`). Error messages never echo values.
  Malicious fixtures: `$(id)`, backticks, newlines, `../`, unicode digits, bidi override, oversize numbers.
* Triggers: `schedule`, `workflow_dispatch`, `push` on `build/**` / `ci-trial/**` only. No `pull_request_target`, no
  `workflow_run`, no comment triggers (tests enforce). All privileged workflows refuse to run off `refs/heads/automation`
  (the wrapper refuses anything except `build/**` and `ci-trial/**`).
* Build branches never contain upstream workflows except the reusable `ci-windows.yml` (`assemble_build.py` strips the
  rest): upstream `push`-triggered workflows would otherwise run in our repository on every pushed build branch.
  Patches that touch `.github/`, `.gitmodules`, submodule gitlinks or the VHF contract pin file are rejected.
* Branch push credentials: `actions/create-github-app-token` with `PUSH_APP_ID` / `PUSH_APP_PRIVATE_KEY`,
  scoped to this repository with `contents:write` + `workflows:write`. If either is missing the job fails with an explicit
  message; there is deliberately no fallback to `GITHUB_TOKEN` or any other credential. Keep the key as an
  **environment** secret of `build-push` restricted to the `automation` branch, so workflows on `build/*` can never read it.
* Moved upstream tag vs `upstream-seen.json` = HALT (job fails, issue + chat alert, nothing built).
* Secret-free upstream inputs: `build_only=false, daily_unsigned=false, daily_validation_only=false, release_channel='',
  release_tag='', publish_symbols=false, upload_symbols_artifact=false, require_signpath_signing=false,
  require_truehdr_runtime=true (public release, no token), release_artifact_retention_days=7, build_tests=true`.
  Never dispatched on a tag ref (the wrapper refuses non-branch refs).
* Sensitive-path gate (R-SEC-5, optional): `sensitive_paths.py` lists changed upstream paths (`.github/`,
  `packaging/windows/`, drivers, submodules, WiX files, lockfiles). Recorded in the run summary always; routes the publish
  job to environment `release-approval` only when repository variable `ENFORCE_SENSITIVE_GATE=true`. `GITHUB_TOKEN`
  cannot read environment protection rules, so the Owner must make sure `release-approval` really has a required reviewer.
* Release assets: `VibepolloSetup.exe`, `Vibepollo.msi`, `SHA256SUMS` (installer, MSI, provenance, manifest; not itself,
  not the bundle), `manifest.json` (schema 1 plus additive keys `msi` and the extra asset `release-provenance.json`),
  `attestation.sigstore.json` (one bundle covering installer, MSI, SHA256SUMS, manifest). Release created as draft,
  `--prerelease --latest=false`, published after every digest matches. Consumers should ignore unknown manifest keys.
* The publish job verifies its own attestation with `gh attestation verify ... --signer-workflow
  <repo>/.github/workflows/patched-windows-release.yml --source-ref refs/heads/build/<tag>-n<N> --deny-self-hosted-runners`
  before the release exists, and `gh release verify` + `verify-asset` after publishing (fails the job loudly when
  immutable releases are not enabled; consumers fail closed in that case).
* Untrusted binary parsing (msiinfo on the upstream-built MSI) runs only in the unprivileged `package` job; its JSON
  summary is size-limited, ASCII-sanitized and re-validated in the publish job before it is embedded in the manifest.

## 5. Operations

| Task | How |
|---|---|
| Dry run of the whole resolve/apply/assemble path | Actions -> Upstream Watch -> Run workflow, `dry_run=true` (needs no App credentials) |
| Build a specific upstream release | `tag=X.Y.Z` (manual runs ignore the "older than newest seen" rule) |
| Pause | `policy.yml: hold: true` or repository variable `HOLD=true`; `override_hold=true` for one run |
| Rebuild with an unchanged series | `force_rebuild=true` -> next `N` |
| Series changed (new commit in `patches/`) | next scheduled/manual run sees a new `series_sha256` -> new `N` automatically |
| Build failed | one automatic `gh run rerun --failed`; afterwards `stuck` alert every run until fixed |
| Patch conflict | run summary + artifact `conflict-report` (failing patch, conflict hunks); fix patches on `automation` |
| Moved upstream tag | investigate; to accept the new commit remove the tag entry from `upstream-seen.json` deliberately |
| Verify a release by hand | `gh release verify <tag> -R emilwojcik93/Vibepollo`; `gh attestation verify VibepolloSetup.exe -R emilwojcik93/Vibepollo --signer-workflow emilwojcik93/Vibepollo/.github/workflows/patched-windows-release.yml --source-ref refs/heads/build/<tag>-n<N> --deny-self-hosted-runners` |
| Local checks | `python -m pytest ci/tests`; `actionlint`; `zizmor .github/workflows` |

Alerts: failures open or comment on ONE issue labelled `automation-alert` (Issues must be enabled in the repo settings;
done for this fork) and, when the secret `GCHAT_WEBHOOK_URL` exists, post a sanitized one-line message
(webhook URL goes through curl `-K -` on stdin, never argv). Missing secret = silent skip.

## 6. Optimization findings (measured from run 45043583952 / 45043834012, windows-2022, public runner)

| Observation | Number | Decision |
|---|---|---|
| `Build Windows` without tests (build_tests=false) | 1534 s (25.6 min) | baseline |
| `Build Windows` with tests (build_tests=true: test targets + ctest) | 2608 s (43.5 min) + 192 s driver-safety tests | tests ON is required for releases; +~21 min. Dry runs may set `tests=false` |
| Checkout incl. recursive submodules | 177-187 s | no safe cache; shallow already |
| setup-msys2 (cache: true) | 121-129 s | already cached by upstream |
| setup-node npm | ~10 s | already cached |
| Packaging jobs (package_windows, finalize) | measured in the trial (see section 8) | required for the bootstrapper |
| Artifact retention | 7 days for everything we upload; upstream's hard-coded installer-stage artifact: 1 day | set in wrapper; repo-wide log retention can also be reduced in Settings |

* **Actions caches are branch-scoped**: a cache written by a run on `build/X-n1` cannot be restored by `build/X-n2` (only
  the same branch and the default branch are readable). Because every build lives on a fresh branch, upstream's msys2/npm
  caches do not help us across builds and a ccache stored from a build branch would never be reused.
* **ccache** (the only large lever: compile is ~25 min of the ~55) would need (a) a patch to upstream `ci-windows.yml`
  (a `ci` patch group + Security Gate review) and (b) a *cache-warmer workflow on the default branch* that builds the
  latest upstream tag once a week to seed the cache in the readable scope. Estimated saving 40-60% of compile time for
  rebuilds of the same upstream release, little for new upstream releases; costs one extra build per week and ~1-2 GB of the
  10 GB cache quota. Not implemented (unproven; changes upstream CI semantics).
* `fail-fast` is irrelevant (single-entry matrix, `fail-fast: false` upstream). Superseded builds (a newer upstream
  release arrives while an older one builds) are not cancelled: concurrency is per build branch with
  `cancel-in-progress: false`, so a started build always finishes or fails on its own.
* Schedules: hourly 07:23-11:23 UTC (upstream publishes ~06:30-07:50 UTC) plus 00/06/12/18 UTC; each poll is ~20 s of
  ubuntu time (3 REST calls) and a no-op when nothing is new.
* Keepalive: weekly `PUT /actions/workflows/{file}/enable` (counts as activity; also revives workflows already in state
  `disabled_inactivity`). Unverified in practice (60-day clock cannot be observed in a session).

## 7. Owner actions (manual, once)

See section 9 of the engineer's report; summarized:
1. Merge `automation-ci` content into `automation`, then make `automation` the fork's default branch
   (`gh repo edit emilwojcik93/Vibepollo --default-branch automation`).
2. Create the GitHub App (Contents: Read & write, Workflows: Read & write, Metadata: Read; no webhook, no user access;
   install on this repository only) and store `PUSH_APP_ID` (variable; the App ID or Client ID) and
   `PUSH_APP_PRIVATE_KEY` (secret) in environment `build-push` (deployment branches: `automation` only).
3. Enable immutable releases (Settings -> General -> Releases).
4. Environments `release-auto` (no rules) and, optionally, `release-approval` (required reviewer = Owner, only if
   `ENFORCE_SENSITIVE_GATE=true`). Optional secret `GCHAT_WEBHOOK_URL`.
5. Rulesets (see report), "Require actions to be pinned to a full-length commit SHA", Dependabot alerts.

## 8. Trial result (unknown U1: secret-free `build_only=false` path)

See the engineer's report for the trial run record (filled in after the run finished).
