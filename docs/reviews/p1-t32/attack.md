# P1-T32 design attack: synthesis

Design: `docs/reviews/p1-t32/design.md`. Lenses: D (EGM design), Z (EGM zoom-out), R (EGM
implementation readiness), S (Opus, operations and security). 2026-11-23, at 24d10a9f0.

There were 55 raw findings. After merging duplicates, 26 remain: **8 high, 12 medium and 6 low**.
I checked every high finding against the code myself, and all 8 hold; the check is noted on each.
No finding was dropped. Changes of severity on merge are noted.

## 1. Findings

### High

**S1 (merges D1, S1). The E4 restore refusal never fires.**
- The problem: §6.2 reads `doc["source"]["manifest"]`, and assumption A10 says the package
  carries it. It does not.
- Checked: `deploy/restore/quarantine.py:204-208` builds `source` from `kind`, `dump_at`, `dump`,
  `kind_of_dump` and `manifest_sha256` only. Nothing puts the manifest, or any field of it, into
  `source`.
- Effect: every new dump takes the "absent" branch and is accepted as "not recorded".
- Why the tests miss it: §8.1's forged-package tests build `source` themselves
  (`make_package(**source)`), so they pass although the real pipeline never supplies the fields.
- Fix:
  - `quarantine.py` copies `environment` and `suffix` from the MAC-verified manifest `m` into
    `source`, and adds `instance_recorded: true|false`.
  - The precheck reads `source["environment"]` and `source["suffix"]`.
  - Confirm that the package digest pinned by `staging.sh` covers `source`.
  - Add `quarantine.py` and `scripts/test_restore_quarantine.py` to §3 and §8.
  - Add one live case (in `test-dumps-live.sh`): a real dumper manifest must produce these fields
    in `package.json`.
  - Correct A10.

**S4 (merges D4, Z1, R4, S4; D4 rated it medium, raised to high). `env_of` kills the script
with no message when a folder has no `.env`.**
- Checked:
  - `staging.sh:136` sets `set -euo pipefail`.
  - The design's `env_of() { [[ -f … ]] && sed …; }` returns 1 when the file is missing.
  - `e="$(env_of "$d")"` then exits the script under errexit, with no message.
  - `env_get` (:192) uses `|| return 0` for exactly this reason.
- Where it breaks:
  - `local-proof` on a fresh workstation;
  - `init --local` or `--vm` into a new `$OUT`;
  - `deploy.sh --local-proof`'s first `init --vm`;
  - every command run on the deploying machine, where `deploy/staging` has no `.env`.
- Fix:
  - Write it as `[[ -f "$1/.env" ]] || return 0; sed … | tail -1`.
  - Add a test with no `.env` in `$DIR` and none in `$OUT`.

**S5 (merges Z2, D3, S5; D3 and S5 rated it medium, Z2 high). E2 fails open on any value it
does not recognise.**
- The problem: `is_production` is the glob `production-*` on the raw `sed` output. These values
  all count as "not production" and pass every E2 refusal and the state check:
  - `"production-1"` (quoted: Compose strips the quotes and runs as production, but `sed` keeps
    them);
  - `production-1 # x`;
  - a line ending in CRLF;
  - `Production-1` or `prod-1`;
  - a deleted line.
- The only backstop is `up` and `release`. Many other paths call `compose up` without them:
  rollback, restore, start-existing, acme, db-roles, monitoring and the dumper (S5 lists 12 call
  sites).
- This contradicts the design's own principle 3 ("a typo can never relax a production rule").
- Fix:
  - Parse strictly: reject quotes, spaces and `\r`.
  - Allow only an exact `staging` or `proof` value. Anything else, an empty value included, gets
    the one-line §4.2 fix and a refusal.
  - The one exception is the §4.5 upgrade case (see M7).
  - Also run `env_state_problems` inside `compose()` for `up`, `run`, `start`, `restart` and
    `create`. Do not run it for `ps`, `down` or `logs`.
  - Add a test row for each malformed form.

**Z3 (merges Z3, R2). The VM cache folder cannot be upgraded as §4.5 and §5.1 describe.**
- Checked:
  - Only `init --local` creates `.local` (`staging.sh:308`).
  - `init --vm` does not create it.
  - `deploy.sh:211` dies on any folder that has `.local`.
- Effect: the existing `$VM_CACHE/staging` folder has no marker the upgrade can key on, so §5.1
  item 2 then refuses it.
- Fix:
  - `deploy.sh --local-proof` upgrades exactly `$VM_CACHE/staging`, and only when its suffix is
    `$PROOF_SUFFIX` and `.env` has no `IHR_ENVIRONMENT`. It appends `proof` and prints a line.
  - From now on, `init --vm` writes `IHR_ENVIRONMENT=proof` itself.
  - Remove the claim that `.local` marks VM folders.
  - Add a test row.

**D2. E3's remote read aborts every first deploy, and could fail open.**
- Checked:
  - `deploy.sh:35` sets `set -euo pipefail`.
  - `remote()` (:273) returns the remote status.
  - The design's remote command ends with `sed … .env 2>/dev/null`. Its exit status is 2 when
    `.env` is missing.
- Effect: `remote … > file` stops the script with no message before the "new server" row can
  apply.
- The reverse risk: an empty output with exit status 0 would be read as "new server".
- Fix:
  - End the remote command with `; echo '#end'; true`.
  - `instance_check.py` requires the `#end` sentinel. A missing sentinel means "cannot read the
    server" and is refused.
  - Add test rows for "no sentinel" and for "new server" (see also S2).

**S2 (merges S2, D6; D6 rated it medium). E3 accepts any fresh server, so production's secrets
can go to a synthetic VM.**
- Checked:
  - §5.2 row "no `DEPLOYED` and no `.env` → ok".
  - `deploy.sh` sends `.env` and `secrets/` as a tar stream (header :26). That includes the dump
    key, the backup passphrase, the bucket keys and the anchor keys.
  - `deploy/host/bootstrap.sh:6-8` has no instance option.
- Effect: `deploy.sh --env-dir ~/indiehr-envs/production-1` aimed at P1-T26's newly built VM, or at
  a mistyped IP, hands production's keys to that host. It could then read production's dumps.
  ADR-020 §3 E3 exists to prevent exactly this.
- Fix:
  - `bootstrap.sh --instance <env> --suffix <s>` writes `/srv/indiehr/INSTANCE`, owned by root and
    not writable by the deploy user.
  - `deploy.sh` refuses, before anything is sent, when `INSTANCE` is missing ("run bootstrap.sh
    --instance first") or names another instance.
  - Remove the "new server ok" row.
  - For a server from before E3 (only the local VM, per A4): `--local-proof` may write `proof`
    over SSH. Any other host must be re-bootstrapped.

**R3 (merges R3, S3). Requiring the environment in the dumper's `Config` breaks the restore path
when no dumper is running.**
- Checked:
  - `dumper.py:1693` builds `cfg = Config()` before it dispatches every subcommand.
  - `dumps_exec` (`staging.sh:5410-5432`), when no dumper is running, runs
    `check|get|manifest|probe` in a one-off container. That container gets only
    `--env-file $SECRETS/dumps.env`.
- Effect: once §6.1 makes `Config` exit on a missing `IHR_ENVIRONMENT`, these break on a lost
  server or on the owner's machine:
  - `hospital-restore` (:5703, :5708);
  - `dumps-get`;
  - `dumps-check`.
- Fix:
  - Pass `-e IHR_ENVIRONMENT=… -e IHR_TENANT_DOMAIN_SUFFIX=…` (from `env_get`) to the one-off.
  - Require the two values only for `run` and `once`. Read commands report "unknown instance".
  - Add a static test: every `dumper.py` invocation in `staging.sh` passes both.

**R1. The IST warning bar would stop working on production.**
- Checked:
  - `views.py:96` always appends `WARNING_SCRIPT`.
  - §7.2 appends `top_bars.BARS_SCRIPT` only when synthetic.
  - §7.3 makes `WARNING_SCRIPT` call `window.__IHR_BARS__`.
- Effect: on `production-*` the host is undefined. The script's catch-all `try` swallows the
  error, and the P1-T7b bar never shows.
- Fix: dissolved by M4 (no shared host). If the host is kept after all:
  - always append `BARS_SCRIPT`, then `WARNING_SCRIPT`, then `SYNTHETIC_SCRIPT`;
  - add a production test that the IST bar renders;
  - fix the `endswith(WARNING_SCRIPT)` assertion (`test_hospital_profile.py:261`).

### Medium

**M1 (merges D5, Z7, S8, R9). One foreign object halts all nightly dumps, with no way back.**
- The problem: Q-K156 refuses every dump when any manifest names another instance, "authentic or
  not". An unauthenticated field that anyone with bucket write access controls, or a stray
  object, stops production's dumps in a Governance-locked bucket (`LOCK_DAYS` 30).
- Gaps:
  - `reason=foreign-instance` is not in the closed `REASONS` tuple (`dumper.py:126`, checked).
  - No alert rule.
  - No recovery step.
- Fix: replace the rule with M2's marker.
  - An authentic foreign marker, or a missing one, refuses.
  - Other foreign or unauthenticated objects become a `check` problem with an alert
    (`reason=foreign-object`), and dumps continue.
  - Add the reasons to `REASONS`, the alerts and the README table.

**M2 (S7). A write to the wrong store is not detected when it happens.**
- The problem: the first dump into a wrong empty bucket goes unnoticed (§10 admits it).
- Fix:
  - `dumps-init` writes `instance.json` (environment and suffix, sealed with the dump key) into the
    bucket.
  - `dumper run` refuses when the marker is missing, fails the MAC or names another instance.
  - A bucket from before P1-T32 (proofs only, A4) gets the marker from `dumps-init --mark`.
- Backups and the log archive do not get a marker in P1-T32. They are listed in §10 as a
  follow-up, because their stores are encrypted per instance, see M3.

**M3 (merges D7, Z4, R8). Cut §6.3 (backup labels).**
- Why:
  - A pgBackRest annotation can be read only when the passphrases were wrongly shared; §6.3 says
    so itself.
  - It is unauthenticated.
  - It rests on A1a and A1b, which are not checked.
  - It costs 4 call sites and a new `repo_instance_check`.
  - The repository path is already per suffix (`repo1-path=/pgbackrest/<suffix>`).
- Fix: move it to §10. E4 is then dumps (manifest fields, marker, restore precheck), and A1a and
  A1b go.

**M4 (merges D8, Z5, R11). Do not refactor the shipped IST bar into a shared top-bar host.**
- The problem:
  - `test_device_time.py` pins many strings of `WARNING_SCRIPT`.
  - The owner confirmed that bar's behaviour (P1-T7b).
  - The host's colours, order, fonts and resize handling are not specified.
  - The refactor created R1.
- Fix:
  - `SYNTHETIC_SCRIPT` draws its own bar at `top:0` and publishes its height in a CSS variable
    (`--ihr-synthetic-h`).
  - The IST bar adds that offset to its `top` and to its reserve rules, a 1–2 line change in
    `device_time.py`.
  - Run the tests-ihr suite with the banner on before anything else.
  - Cap the layout fixes at about 0.5 h. Past the cap, fall back to "servers only".

**M5 (Z6). The 12 guards inside functions duplicate the state check.**
- Fix: keep both, as ADR-020 E2 asks, but put the command guard in one place:
  - a table of the 12 subcommand names checked once at dispatch in `main`, not 12 edits inside
    functions;
  - the static test checks the table.
- Drop `acme-proof-run` from the table (see L6).

**M6 (merges R6, S6). The state check is incomplete.**
- Missing from §4.4:
  - the proof suffix `ihr-proof.in` (§4.2 item 3);
  - `IHR_SMOKE_RESOLVE`;
  - `IHR_BIND=127.0.0.1`;
  - an anchor endpoint `http://s3:8333` in `secrets/anchor-*.env` (the fingerprint bucket on the
    same VM; ADR-020 §1.3);
  - file or picture bucket names containing `staging` on production. `init` writes
    `ihr-staging-*` today; ADR-020 §1.2 forbids that name on production.
- Fix:
  - Keep one `env_state_problems` list, used by `init --check`, `compose()` start paths
    (see S5), `release` and `deploy.sh`.
  - `init --environment production-<n>` writes `ihr-prod<n>-files` and `ihr-prod<n>-pictures`.
  - On `staging`, refuse bucket names containing `prod`.
  - Test every marker, and correct the "six markers" count in §8.2.

**M7 (merges D10, Z9, S12; D10 and S12 rated it low). Folders from before E1 break on every
Compose command.**
- The problem: `${IHR_ENVIRONMENT:?}` (A3) also fails `down`, `ps` and `logs`. An existing proof
  stack on this workstation cannot even be stopped until `local-proof` runs.
- Fix: one `ensure_environment` call in the `compose()` wrapper (`staging.sh:~143-187`), before
  the first `docker compose`:
  - a `.local` folder with no value is upgraded to `proof`, with a printed line;
  - any other folder gets the one-line fix.

**M8 (merges D11, Z8). Disaster recovery cannot restore across a changed domain suffix.**
- The problem: the §6.2 precheck refuses a dump whose suffix differs, with no override. A rebuilt
  production-1 on a new domain then cannot restore its own dumps.
- Fix:
  - The `environment` must match, and a mismatch is refused.
  - A differing suffix with the same environment is a warning, recorded in the restore report.
  - New decision Q-K159.

**M9 (merges D9, Z10, R13, S9's first half; Z10, R13 and S9 rated it low). The `smoke` banner
check tests the wrong thing.**
- The problem:
  - It requests the JSON, but the banner lives only in `bootstrap.js` and the stub.
  - Comparing against the same `IHR_ENVIRONMENT` is plumbing only.
  - A held hospital answers 503 on `/api/` and gives a false FAIL.
  - The request lacks `curl_args` and `:$port` (`staging.sh:1486-1499`).
- Fix:
  - Fetch `bootstrap.js` through `curl_args` and `:$port`.
  - Require `ihr-synthetic` when not production and its absence on production.
  - Skip held hospitals.
  - Drop the JSON comparison.

**M10 (merges R5 and S9's second half). The banner on the release gate's stub is invisible.**
- Checked: the overlay is opaque and full-screen at `z-index:2147483647`
  (`release_gate.py:169`). §7.3 puts the bar one below it.
- Effect: nothing can be typed under the overlay anyway.
- Fix: drop the stub banner and its test. Change Q-K157 to match.

**M11 (merges R7, S10). `instance_check.py` is not specified, and the table has gaps.**
- Fix:
  - argv: `<env> <suffix> <file>`. Exit 0 means ok; exit 1 means refused, with the reason on
    stdout. No `instance.why` file.
  - Precedence: every source present (`INSTANCE`, `DEPLOYED`, the server `.env`) must agree;
    any disagreement is refused.
  - Add rows: `DEPLOYED` says X but `.env` says Y; an environment without a suffix; an empty
    `DEPLOYED` with a `.env` present.
  - A legacy `DEPLOYED` with no environment does not silently record the folder's label. It is
    refused unless `INSTANCE` exists (see S2).

**M12 (R10). `settings.IHR_ENVIRONMENT` does not exist in dev or test settings.**
- Effect: the §6.2 precheck would raise `AttributeError` in `test_restore`.
- Fix:
  - Use `environment.name()` (`dev`).
  - Name the suffix's source.
  - State what the forged-package tests expect in dev.

### Low

**L1 (merges D12, Z11, R12, S11). Citations that are wrong.**
- `up` is at `staging.sh:1329`, not :1257. `release` is at :2452.
- The audit-supersede `IHR_ENVIRONMENT` is at `compose.yaml:339`; checked, and the defaults are at
  `compose.yaml:42,339` and `compose.monitoring.yaml:237`.
- `problems()` returns at `secure.py:151`.
- The console `STYLE` is at `console/views.py:43`.
- `test_staging_operator.py` has no "JobTier list".
- `ihr_platform/templatetags/` needs an `__init__.py`.
- The console's Hindi `<div>` has no class, so it renders unstyled under `style-src 'self'`.
- The §4.4 marker count is wrong.
- `compose.dumps.yaml` and `compose.backup.yaml` have no `IHR_ENVIRONMENT` line today, so those
  entries are additions.

**L2 (merges D13, Z14). The estimate and the split.**
- S2 is too large.
- S6 hides the local proof.
- S3 ∥ S4 conflicts on `staging.sh`.
- The new split is in §2.

**L3 (merges D14, R15). Two dead or missing items.**
- `window.__IHR_ENVIRONMENT__` has no reader. Drop it; that means fewer public globals and
  matrix or allowlist changes.
- The usage header (`staging.sh:12`) and the roughly ten `run: init <suffix>` die messages need
  the new `init` syntax.

**L4 (merges Z12, R14). The name regex is copied into six files.**
- Fix:
  - Say which copy is canonical (`environment.py`).
  - Add one test that extracts every copy and asserts they are equal.
  - `environment.py` must not import `django.conf` at import time, because settings modules import
    it.

**L5 (Z13).** Bash, `sed` pipelines and line numbers belong in the subtask briefs, not the
design. This invited S4 and L1. Keep §4 and §5 at the interface level.

**L6 (Z15).** The guard in `acme-proof-run` is always satisfied: it runs inside its own `proof`
copy. The real guard is in `acme-proof`. Drop `acme-proof-run` from the table and from the "12"
count.

## 2. Orchestrator decision (accepted by the orchestrator, 2026-11-23)

Binding: where this section and design.md differ, this section wins.

### Changes the design needs (amend design.md before S1)

1. **E1 and E2 parsing (S4, S5, L4).**
   - `env_of` uses `|| return 0`.
   - Parsing is strict and fails closed: only exact `staging` or `proof` pass the E2 guards.
   - One canonical regex, with an equality test.
2. **E2 structure (M5, M6, M7, L6).**
   - One dispatch table of guarded subcommands.
   - One `env_state_problems` list, with the added markers and the bucket-name rule.
   - `compose()` runs `ensure_environment` on every call, and `env_state_problems` on start
     verbs.
3. **VM proof (Z3).** `init --vm` writes `proof`. `deploy.sh --local-proof` upgrades exactly
   `$VM_CACHE/staging` under the stated conditions.
4. **E3 (S2, D2, M11).**
   - `bootstrap.sh --instance/--suffix` writes a root-owned `INSTANCE`.
   - `deploy.sh` refuses a missing or different `INSTANCE` before sending secrets.
   - The remote read has an `#end` sentinel.
   - Every source must agree.
   - `instance_check.py` gets a fixed argv and exit codes.
   - The "new server ok" row is removed.
5. **E4 dumps (S1, R3, M1, M2, M12).**
   - The quarantine copies `environment`, `suffix` and `instance_recorded` into `source`.
   - The precheck uses `environment.name()`.
   - The dumper requires the instance only for `run` and `once`.
   - `dumps_exec` one-off passes both values.
   - A bucket marker `instance.json` decides ownership.
   - New `REASONS`, alert and README rows.
6. **Cut §6.3** (M3). Backup and log-archive labels go to §10. Drop A1a and A1b.
7. **E5 (R1, M4, M9, M10, L3).**
   - No shared top-bar host. The synthetic bar is its own script, and the IST bar is offset by a
     CSS variable.
   - No stub banner.
   - No `__IHR_ENVIRONMENT__` global.
   - `smoke` checks `bootstrap.js`.
   - Layout fixes capped at 0.5 h, then fall back to servers only.
8. **Restore across suffixes (M8).** The environment is strict; the suffix only warns.
9. **Records (L1, L3).** Fix the citations. Add the usage header and die strings to §3.

### Decisions (an unattended run auto-accepts the recommended option)

| Q-ID | Recommended | Change from the design |
|---|---|---|
| Q-K150 Environment names | A | none |
| Q-K151 How `init` learns it | A, amended | `init --vm` writes `proof` too; production folders get `ihr-prod<n>-*` bucket names |
| Q-K152 Folders from before E1 | A, amended | the upgrade runs in `compose()` (every command); the VM cache upgrades by exact path and suffix, not `.local` |
| Q-K153 E2's reach | A, amended | one dispatch table plus the state check on every Compose start verb; parsing fails closed |
| Q-K154 E3 rules | **C (new)**: A plus host identity | `bootstrap.sh --instance` writes a root-owned `INSTANCE`; missing or different refuses; no "new server ok"; no override |
| Q-K155 Older dumps and backups | A | backups drop out of P1-T32 (§6.3 cut) |
| Q-K156 Foreign objects in the bucket | **C (new)**: marker-based | an authentic `instance.json` decides; a missing or foreign marker refuses; a stray foreign object is a `check` problem and an alert, and dumps continue |
| Q-K157 Where the banner shows | A, amended | no stub banner (overlay covers it); no `__IHR_ENVIRONMENT__` global; the JSON keeps `synthetic` |
| Q-K158 Dev and layout | **C (new)** | dev and CI show the banner; no shared host (own bar plus a CSS-variable offset for the IST bar); fallback B after a 0.5 h layout cap |
| Q-K159 (new) Restore across suffixes | A | the environment must match; a different suffix warns and is recorded (disaster recovery to a new domain) |

### Review shape

- S2 and S3 change where secrets may go.
- S4 changes a MAC-checked object and a restore refusal.
- So: Opus high builders for S3 and S4; one Opus high review (operations and security lens) of
  the whole diff; Opus high fixers for any finding on the `INSTANCE` check, the marker or the
  restore precheck.
- No access, tenancy or audit-chain controls change, so the full 3-lens shape is not needed.

### Split (about 11.5 agent hours)

| Step | Content | Depends on | Builder | Hours |
|---|---|---|---|---|
| S1 | E1: `environment.py`, `secure.py`, settings, regex-equality test | none | Sonnet high | 1.0 |
| S2a | `staging.sh` E1: strict `env_of`; `init --environment/--local/--vm` and `--check`; bucket names; `ensure_environment` in `compose()`; Compose `:?`; fixtures; usage text | S1 | Sonnet high | 1.25 |
| S2b | `staging.sh` E2: dispatch table; `env_state_problems` (all markers) on start verbs, `release` and `init --check`; `smoke` `bootstrap.js` check; drill env; `dumps_exec` one-off env | S2a | Sonnet high | 1.25 |
| S5 | E5: `SYNTHETIC_SCRIPT` plus the IST offset; bootstrap and console; matrix keys; Playwright spec; Hindi review sheet (layout cap 0.5 h) | S1 | Sonnet high | 1.5 |
| S3 | E3: `deploy.sh` checks, `instance_check.py`, `bootstrap.sh --instance`, VM upgrade, tests, host README | S2a | Opus high | 2.0 |
| S4 | E4: dumper fields, marker, `REASONS` and alerts; quarantine `source`; restore precheck (Q-K159); compose env; unit and live tests | S1, after S2b (`staging.sh`) | Opus high | 2.0 |
| S6 | Whole-diff review (operations and security) and fixes | S1–S5 | Opus high review; fixers per routing | 1.5 |
| S7 | §8.5 local proof, CI, records (ADR-020 E1–E5 "Already enforced", TRACKER, QUESTIONS Q-K150…Q-K159) | S6 | Sonnet high | 1.0 |

- Order: S1 → S2a → (S2b ∥ S5) → (S3 ∥ S4) → S6 → S7.
- S3 and S4 do not share files: S3 owns `deploy.sh` and `bootstrap.sh`; S4's `staging.sh` edits
  are folded into S2b.
- The estimate stays near the design's 11 h, against ADR-020's 4–6 h guess:
  - cutting §6.3 and the top-bar host saves about 1.5 h;
  - host identity (S2) and the bucket marker (M2) close two real gaps and add about 1 h;
  - the local proof gets its own step.
- First cut if the run must shrink: the `staging`/`prod` bucket-name rule (M6, last two bullets),
  then the Playwright spec (keep the unit tests).
