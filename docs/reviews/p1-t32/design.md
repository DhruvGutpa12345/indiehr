# P1-T32 No-real-data checks E1–E5: design

Status: final after attack (docs/reviews/p1-t32/attack.md, Orchestrator decision)

Finding ids in brackets (S1, M4, …) are from the attack; where its Orchestrator decision and this file differ, the decision wins.

Source: ADR-020 §3 "Not yet enforced" (Accepted by the owner, 2026-11-23). Five checks that turn
the no-real-data rule from a convention into refusals. Line numbers are as of commit 24d10a9f0.
Decision IDs Q-K150 upward (P1-T31 uses Q-K144…Q-K149).

This task writes no new `staging.sh` subcommand (the list is frozen: `deploy/staging/subcommands.txt`,
`scripts/test_staging_surface.py`). Everything below is a new option on an existing subcommand,
a new check inside one, or code outside `staging.sh`. Every privileged call stays inside the five
operator wrappers (`scripts/test_staging_operator.py`).

## 1. Principles

1. **One source of truth per instance: `IHR_ENVIRONMENT` in the instance's `.env`.** `init`
   writes it once. Compose passes it to every process that needs it. Nothing defaults it any more.
2. **Refuse, never guess.** A folder or server without a recognisable environment is refused,
   with the one-line fix. The only automatic upgrade is to folders that are proofs by construction.
3. **Production is the special case.** "Synthetic" means "not `production-*`". Every check asks
   "is this production?", so a typo in a non-production name can never relax a production rule.
4. **A refusal names both instances.** The point of E3 and E4 is the message:
   "this is production-1 (app.amhcloud.in); the folder is staging (app.stg.amhcloud.in)".
5. **No Keycloak, frontend-bundle or CARE change.** E5 rides on `bootstrap.js`, which the web app
   already loads before its own code (`apps/frontend/index.html:30`). The web app is not rebuilt.
6. **Parsing fails closed [S5].** Only an exact `staging` or `proof` value passes an E2 guard;
   anything else (quotes, spaces, `\r`, a wrong case, an empty value) is refused with the fix.

## 2. Environment names (E1, Q-K150)

| Value | Where | Synthetic? |
|---|---|---|
| `production-<n>` (`n` 1–999, no leading zero) | production-1, later cells | no |
| `staging` | a synthetic server, e.g. P1-T26's rented VM (ADR-020 §2 A) | yes |
| `proof` | `staging.sh local-proof` on this workstation, the throwaway VM of `deploy.sh --local-proof`, `acme-proof`'s throwaway copy | yes |
| *(unset)* | dev (`care-dev.sh`), tests, CI | yes (reported as `dev`) |

The regular expression, used everywhere (Python and bash): `^(production-[1-9][0-9]{0,2}|staging|proof)$`.
The canonical copy is in `environment.py`; one test extracts every other copy and asserts they are
equal [L4]. `environment.py` must not import `django.conf` at import time, because settings
modules import it.

Dev and test settings **must not** set `IHR_ENVIRONMENT`: `db_roles.off_problems`
(`ihr_platform/db_roles.py:1581`) treats any non-empty value as "on a server" and would then
refuse dev's superuser login. The helper below reports unset as `dev`.

## 3. Files

| File | Change | Check |
|---|---|---|
| `plugins/ihr_platform/ihr_platform/environment.py` (new) | canonical `SERVER_RE`, `name()` (setting or `"dev"`), `is_production(name)`, `synthetic()`, `BANNER_EN`, `BANNER_HI`; no `django.conf` import at import time | E1, E5 |
| `ihr_platform/settings/secure.py` | `"IHR_ENVIRONMENT"` in `REQUIRED_ENV`; new `environment_problems(settings)` called from `problems()` (return at :151) and from `watch_problems()` (:340) | E1 |
| `ihr_platform/settings/deployment.py:32`, `watch.py:32`, `drill.py:50` | `default="staging"` → `default=""` (secure.py and the drill check then refuse it); `drill.py` refuses itself if the value fails `SERVER_RE` | E1 |
| `deploy/staging/compose.yaml:42` and :339 (audit-supersede), `compose.monitoring.yaml:237` | the defaults become `IHR_ENVIRONMENT: ${IHR_ENVIRONMENT:?set IHR_ENVIRONMENT in .env (ADR-020 E1)}` | E1 |
| `deploy/staging/compose.dumps.yaml`, `compose.backup.yaml` | **additions** (no `IHR_ENVIRONMENT` line exists today): `IHR_ENVIRONMENT` as above; the dumper also gets `IHR_TENANT_DOMAIN_SUFFIX: ${IHR_TENANT_DOMAIN_SUFFIX:?}` | E1, E4 |
| `scripts/staging.sh` | §4 (E1, E2), `dumps_exec` one-off env (§6), `hospital_restore_drill` passes `-e IHR_ENVIRONMENT=… -e IHR_TENANT_DOMAIN_SUFFIX=…` from `env_get`, usage header (:12) and the roughly ten `run: init <suffix>` die messages updated to the new `init` syntax [L3] | E1, E2, E4 |
| `scripts/deploy.sh`, `deploy/host/bootstrap.sh` | §5 | E3 |
| `scripts/instance_check.py` (new, stdlib, `python3 -I`) | `compare()` of §5.2; run only on the deploying machine | E3 |
| `deploy/dumps/dumper.py` | §6.1 | E4 |
| `deploy/restore/quarantine.py`, `scripts/test_restore_quarantine.py` | copy `environment`, `suffix`, `instance_recorded` from the MAC-verified manifest into `source` (§6.2) [S1] | E4 |
| `ihr_platform/restore/engine.py` `Restore.precheck` (:495) | §6.2 | E4 |
| `ihr_tenant_settings/device_time.py`, `synthetic_bar.py` (new, `SYNTHETIC_SCRIPT`), `views.py` (`bootstrap` :74, `bootstrap_js` :78) | §7 | E5 |
| `ihr_platform/templatetags/__init__.py` (new) and `ihr_environment.py` (new), `templates/ihr_console/base.html`, `console/views.py:43` (`STYLE`) | console banner (§7.4) | E5 |
| `ihr_access/tests/role_matrix_keys.txt:68`, `ihr_platform/tests/isolation/anonymous_allowlist.txt:27` | the new `synthetic` key; comment says "and whether this is a synthetic instance" | E5 |
| Test fixtures that write a `.env`: `scripts/test_staging_s3.py`, `test_staging_surface.py`, `test_staging_db_roles.py`, `test_staging_monitoring.py`, `scripts/check-monitoring.sh`, `scripts/load-test.py`, `scripts/test-dumps-live.sh` | add `IHR_ENVIRONMENT=proof` | E1 |
| `deploy/staging/README.md`, `deploy/host/README.md`, ADR-020 §3, `docs/TRACKER.md`, `docs/QUESTIONS.md`, `docs/i18n/review/` (banner's Hindi) | runbook and records; the dumper `reason=` table and an alert row (§6.1) | all |

Cut (attack M3): the backup-label rows (`compose.backup.yaml` annotations, `schedule.sh`,
`repo_instance_check`). They move to §10.

## 4. E1 and E2 in `scripts/staging.sh`

Interface level; the subtask brief holds the shell detail [L5].

### 4.1 Helpers (near `env_get`)

- `ENV_RE='^(production-[1-9][0-9]{0,2}|staging|proof)$'` (one canonical regex, equality test, §2).
- `env_of <folder>`: **`[[ -f "$1/.env" ]] || return 0`** (as `env_get` does with `|| return 0`; with
  `set -euo pipefail` a missing file would otherwise kill the script with no message) [S4], then
  `sed … | tail -1`. Parsing is **strict and fails closed** [S5]: quotes, spaces, `\r`, a wrong
  case or a malformed line are never read as "not production". Only exact `staging` or `proof`
  pass an E2 guard; `production-<n>` matches only by the regex; anything else, an empty value
  included, is refused with the §4.2 one-line fix. The one exception is the §4.5 upgrade case.
- `refuse_on_production <what>` checks both `$DIR` and `$OUT` (on a server they are the same
  folder; on the deploying machine `IHR_STAGING_OUT=~/indiehr-envs/production-1` points `$OUT` at
  production-1's folder while `$DIR` is the repository's synthetic one; either being production,
  or unreadable, is enough to refuse). Tests: no `.env` in `$DIR` and none in `$OUT`; one row per
  malformed form (quoted, trailing comment, CRLF, `Production-1`, `prod-1`, deleted line).

### 4.2 `init`

- New syntax: `init <suffix> --local | --vm | --environment <production-<n>|staging>`.
  `--local` and `--vm` write `IHR_ENVIRONMENT=proof` (**`init --vm` writes `proof` too** [Z3]).
  Plain `init <suffix>` with no option is refused ("say which environment: --environment
  production-<n>|staging, or --local/--vm for a proof; ADR-020 §3 E1"). `--environment proof` is
  refused (a proof is `--local` or `--vm`). `--local`/`--vm` together with `--environment` is
  refused. (Q-K151)
- Writes `IHR_ENVIRONMENT=<value>` as the second line of `.env`, before the suffix.
- **Bucket names [M6]:** `init --environment production-<n>` writes `ihr-prod<n>-files` and
  `ihr-prod<n>-pictures` (ADR-020 §1.2 forbids `staging` in a production bucket name; today `init`
  writes `ihr-staging-*`). On `staging`, bucket names containing `prod` are refused.
- **`init --check`** (new option, no suffix): reads `$OUT/.env` and prints `ok <env> <suffix>` or
  every problem, exit 1: a missing or malformed `IHR_ENVIRONMENT` (with the fix: "add
  `IHR_ENVIRONMENT=staging` or `production-<n>` to `$OUT/.env`; a proof folder is upgraded by
  local-proof itself"); and, with a `production-*` value, every `env_state_problems` entry (§4.4).
  `deploy.sh` runs it like `db-split-secrets --check` (§5.1). It reaches no operator wrapper and
  opens no operator session, like `acme-proof`; `scripts/test_staging_operator.py` has no JobTier
  list, so a static test asserts `init --check` calls no wrapper [L1].

### 4.3 E2: the commands refused on production

**One dispatch table** of guarded subcommands, checked once at dispatch in `main` (not edits
inside twelve functions) [M5]. Each entry calls `refuse_on_production "<command line>"` on its
stand-in branch only; the static test checks the table, and the §8.2 test fails if a table entry
or its call is removed.

| Command (stand-in branch) | Note |
|---|---|
| `local-proof` | |
| `acme-proof` | the real guard is here; it also writes `IHR_ENVIRONMENT=proof` into its throwaway `.env` |
| `acme-init pebble`, `acme-init letsencrypt-staging` | after the `case "$ca"` |
| `mail-init --test` | when `test=1` |
| `monitoring-init --test` | when `test=1` |
| `init --local`, `init --vm` | the `$OUT` folder is new, so the check is on `$DIR`; `--local`/`--vm` with `--environment production-*` is refused |
| `backup-init --local` | when `local_repo=1` |
| `logs-archive-init --local` | when `local_arc=1` |
| `dumps-init --local` | when `local_d=1` |

`acme-proof-run` is **not** in the table: it runs inside `acme-proof`'s own `proof` copy, so its
guard would always be satisfied [L6]. Not refused: `s3-init` (SeaweedFS for patient files on
production-1's own VM is allowed, ADR-020 §1.3); `dumps-expire --allow-short` (already refused
outside a proof's stand-in, `STAND_INS`).

### 4.4 The state check (Q-K153)

The command check stops new stand-ins. A production `.env` copied from a proof folder, or edited by
hand, needs a check of the folder's state. **One `env_state_problems <folder>` list** [M6], used by
`init --check`, the start verbs (below), `release` and `deploy.sh`, lists for a `production-*`
folder:

- `IHR_ACME` is `pebble` or `letsencrypt-staging`;
- `IHR_MAIL=test`, `IHR_MONITORING=test`;
- `IHR_BACKUP_ENDPOINT=http://s3:8333`, `IHR_DUMPS=local`, `IHR_LOGS_ARCHIVE=local`;
- `.local` exists in the folder;
- the proof suffix (`ihr-proof.in`, `LOCAL_SUFFIX`);
- `IHR_SMOKE_RESOLVE` is set; `IHR_BIND=127.0.0.1`;
- an anchor endpoint `http://s3:8333` in `secrets/anchor-*.env` (the fingerprint bucket on the same
  VM; ADR-020 §1.3);
- file or picture bucket names containing `staging`. On `staging`, names containing `prod` are
  refused.

Every marker has a test row. **`compose()` runs `ensure_environment` on every call and
`env_state_problems` on the start verbs** (`up`, `run`, `start`, `restart`, `create`), not on `ps`,
`down` or `logs` [S5, M7]. That covers rollback, restore, `start-existing`, acme, db-roles,
monitoring and the dumper, which call `compose up` without `up` or `release`. `up` (:1329) and
`release` (:2452) die on any marker and on a missing or invalid `IHR_ENVIRONMENT` (the Compose `:?`
would stop them anyway, but with a worse message).

### 4.5 Folders made before E1 (Q-K152)

`${IHR_ENVIRONMENT:?}` fails every Compose command, `down`, `ps` and `logs` included (A3), so an
existing proof stack could not even be stopped. Therefore the upgrade runs in **`compose()`, on
every command** [M7]:

- `ensure_environment`: a folder with `.local` and no `IHR_ENVIRONMENT` is a proof by construction
  (`init --local` made it): it appends `IHR_ENVIRONMENT=proof` and prints "upgraded this proof
  folder: IHR_ENVIRONMENT=proof (ADR-020 E1)". Any other folder gets the one-line fix.
- **The VM cache folder** (`$VM_CACHE/staging`, made by `init --vm`) has no `.local` marker
  (`deploy.sh:211` dies on any folder that has one) [Z3]. `deploy.sh --local-proof` upgrades
  exactly `$VM_CACHE/staging`, and only when its suffix is `$PROOF_SUFFIX` and `.env` has no
  `IHR_ENVIRONMENT`: it appends `proof` and prints a line. From now on `init --vm` writes
  `IHR_ENVIRONMENT=proof` itself. (The earlier claim that `.local` marks VM folders is removed.)
- Nothing writes `staging` or `production-*` automatically.

Today no server other than the local VM exists (P1-T26 is not done; TRACKER), so the hard stop
touches only proof folders, which upgrade themselves.

### 4.6 `smoke`

- First line: `environment: <env> (suffix <suffix>)`.
- For each active hospital it already reaches and that is **not held** [M9], fetch
  `https://<slug>.<suffix>/api/ihr/bootstrap.js` through `curl_args` and `:$port` (the same way
  the other requests there do). It must contain `ihr-synthetic` when the environment is not
  production and must not on `production-*`; otherwise `FAIL  <slug>: banner state does not match
  IHR_ENVIRONMENT`. The JSON comparison is dropped: the banner lives in `bootstrap.js`. This is
  E5's live check.

## 5. E3 in `scripts/deploy.sh`

### 5.1 Before connecting

After the existing `$ENV_DIR/.local` check:

1. `IHR_STAGING_OUT="$ENV_DIR" scripts/staging.sh init --check` must pass (§4.2): a valid
   environment and, on production, no stand-ins.
2. `--local-proof` needs `IHR_ENVIRONMENT=proof` in `$ENV_DIR` (after the §4.5 upgrade). Without
   `--local-proof`, `proof` is refused: a proof folder never goes to a real host.
3. A `production-*` folder inside the repository checkout (`realpath "$ENV_DIR"` under `$ROOT`)
   is refused (ADR-020 §1.4: production-1's folder lives outside the repository).

### 5.2 Against the server (Q-K154: C, A plus host identity)

**Host identity [S2]:** `deploy/host/bootstrap.sh --instance <env> --suffix <s>` writes
`/srv/indiehr/INSTANCE`, owned by root and not writable by the deploy user. `deploy.sh` refuses,
**before anything is sent** (the tar stream carries `.env` and `secrets/`: the dump key, backup
passphrase, bucket keys and anchor keys), when `INSTANCE` is missing ("run bootstrap.sh --instance
first") or names another instance. For a server from before E3 (only the local VM, per A4),
`--local-proof` may write `proof` over SSH; any other host must be re-bootstrapped.

Right after "Checking $HOST", and before the release-log reads and the `DEPLOYING` marker, one
remote read of `INSTANCE`, `DEPLOYED` and the server's `.env` (the two keys), written to a file in
`$CM_DIR`. The remote command ends with `; echo '#end'; true` [D2]: `deploy.sh` runs under `set
-euo pipefail`, and a missing `.env` would otherwise stop the script with no message, while an
empty output with exit 0 could be read as "new server". `instance_check.py` **requires the `#end`
sentinel**; a missing sentinel means "cannot read the server" and is refused.

`instance_check.py <env> <suffix> <file>`: exit 0 means ok; exit 1 means refused, with the reason
on stdout (no `instance.why` file) [M11]. **Every source present (`INSTANCE`, `DEPLOYED`, the
server `.env`) must agree**; any disagreement is refused.

| Server holds | Result |
|---|---|
| no `#end` sentinel | refused: "cannot read the server" |
| `INSTANCE` missing | refused: "run bootstrap.sh --instance first" (there is **no "new server ok" row**) |
| `INSTANCE` present, every other source agrees with the folder's environment and suffix | ok |
| `DEPLOYED` with `environment=` and `suffix=` | ok only if both equal the folder's (and `INSTANCE`) |
| `DEPLOYED` says X but the server `.env` says Y | refused |
| an environment without a suffix | refused |
| an empty `DEPLOYED` with a `.env` present | refused |
| `DEPLOYED` without `environment=` (a deploy before E3) | refused unless `INSTANCE` exists; the folder's label is never recorded silently |
| a suffix or environment that differs, from any source | refused: "this server runs <env or unknown> at <suffix>; the folder is <env> at <suffix>: an instance is never deployed over another (ADR-020 §3 E3)" |

There is no override option. A server changes instance only by being rebuilt
(`deploy/host/bootstrap.sh` on a fresh VM; ADR-020 §1: production-1 is built fresh). A deploy that
failed half-way leaves `DEPLOYING` and the old `DEPLOYED`; the server `.env` it may have replaced
was checked first.

### 5.3 Recording

The `DEPLOYED` block gets `environment=$(env_get IHR_ENVIRONMENT)` next to `suffix=`.

## 6. E4: dumps say which instance made them

Cut (attack M3): backup labels (pgBackRest annotations). The annotation is unauthenticated, readable
only when passphrases were wrongly shared, rests on unchecked assumptions A1a/A1b, costs four call
sites and a new `repo_instance_check`, and the repository path is already per suffix
(`repo1-path=/pgbackrest/<suffix>`). Backups and the log archive get no marker in P1-T32 (§10).

### 6.1 Dump manifests and the bucket marker (`deploy/dumps/dumper.py`)

- `Config` reads `IHR_ENVIRONMENT` (must match the regex) and `IHR_TENANT_DOMAIN_SUFFIX`
  (non-empty): `self.instance = (env, suffix)`. **They are required only for `run` and `once`**
  [R3]; read commands (`check`, `get`, `manifest`, `probe`) report "unknown instance" when they are
  absent. Otherwise `hospital-restore`, `dumps-get` and `dumps-check` would break on a lost server
  or the owner's machine, where the one-off container gets only `--env-file $SECRETS/dumps.env`.
  `staging.sh` `dumps_exec`'s one-off passes `-e IHR_ENVIRONMENT=… -e IHR_TENANT_DOMAIN_SUFFIX=…`
  (from `env_get`); a static test asserts every `dumper.py` invocation in `staging.sh` passes both.
- `dump_one`: the manifest dict gets `"environment": env, "suffix": suffix` before
  `sealer.mac(manifest)`, so both are authenticated (`body_mac` covers the whole body, A2).
  `FORMAT` stays `ihr-dump/1`: the fields are additions; `valid_manifest` does not require them.
- **Bucket marker [M2, Q-K156 C]:** `dumps-init` writes `instance.json` (environment and suffix,
  sealed with the dump key) into the bucket. `dumper run` refuses when the marker is **missing,
  fails its MAC, or names another instance**. A bucket from before P1-T32 (proofs only, A4) gets
  the marker from `dumps-init --mark`. This catches a first-ever dump into a wrong empty bucket.
- **Foreign objects [M1]:** a stray foreign or unauthenticated manifest no longer halts all nightly
  dumps (it would, in a Governance-locked bucket with `LOCK_DAYS` 30, with no way back). It
  becomes a `check` problem with the unauthenticated words "written by <env> (<suffix>), this is
  <env> (<suffix>): dumps never cross instances (ADR-020 §3). Unverified: it does not check with
  this instance's key" and an alert (`reason=foreign-object`); dumps continue. An authentic
  manifest naming another instance is listed in `bad` with "authentic but from <env> (<suffix>):
  this instance holds another instance's dump key, a key incident (ADR-020 §2)". Fields absent (a
  manifest from before P1-T32): valid as today (Q-K155).
- The marker refusal and the foreign-object reason are added to the closed `REASONS` tuple
  (`dumper.py:126`), with a new alert rule and rows in the `reason=` table of
  `deploy/staging/README.md`.
- `check` prints the reasons; `manifest` exits with the reason for that hospital instead of "has
  problems".

### 6.2 Quarantine and restore pre-check

- **Quarantine [S1]:** `deploy/restore/quarantine.py` (`source` is built from `kind`, `dump_at`,
  `dump`, `kind_of_dump` and `manifest_sha256` only; nothing carries the manifest) copies
  `environment` and `suffix` from the **MAC-verified** manifest `m` into `source` and adds
  `instance_recorded: true|false`. Confirm that the package digest pinned by `staging.sh` covers
  `source`. Add `quarantine.py` and `scripts/test_restore_quarantine.py` to the tests, and one live
  case in `scripts/test-dumps-live.sh`: a real dumper manifest must produce these fields in
  `package.json`. (A10 is corrected below; the forged-package tests build `source` themselves and
  would pass even if the pipeline never supplied the fields.)
- **Pre-check [M12, Q-K159]:** in `Restore.precheck` (:495), after the quarantine report check and
  before the transaction-id rule, read `doc["source"]["environment"]`, `["suffix"]` and
  `["instance_recorded"]`. Compare against **`environment.name()`** (`dev` in dev and test; there is
  no `settings.IHR_ENVIRONMENT` there) and `settings.IHR_TENANT_DOMAIN_SUFFIX` (the suffix's source;
  builder confirms the setting exists in dev and test and reads it defensively). The forged-package
  tests state what they expect under `dev`.
  - Environment recorded and different: `Refused("this dump was made by <env> (<suffix>); this is
    <env> (<suffix>): dumps never cross instances (ADR-020 §3)")`. **The environment is strict.**
  - Same environment, different suffix: a **warning**, recorded in the restore report (disaster
    recovery to a new domain; a rebuilt production-1 must restore its own dumps).
  - Not recorded: the report notes `"environment": "not recorded (dump older than P1-T32)"`; the
    key and chain checks still apply (Q-K155).
  - `--from-pitr` packages have no manifest: unchanged.
- The drill (`settings.drill`) compares against the live instance's values, which `staging.sh`'s
  `hospital_restore_drill` passes into its one-off. Without that, production-1's drill would read
  `staging` and refuse its own dump.

## 7. E5: the synthetic banner

### 7.1 Text (fixed; Q-K157)

`ihr_platform/environment.py`:

- `BANNER_EN = "Synthetic data only: do not enter real patients."` (ADR-020 §3 wording).
- `BANNER_HI = "केवल कृत्रिम (सिंथेटिक) डेटा: असली मरीज़ों की जानकारी दर्ज न करें।"`. This goes into
  `docs/i18n/review/` for the owner and is checked against `docs/i18n/hi-glossary.md`.

The text lives in Python, not `deploy/locale/`: `bootstrap.js` runs before the web app's i18n
loads, and the console is Django. This is the IST bar's pattern (`device_time.py`).

### 7.2 Public bootstrap (`ihr_tenant_settings/views.py`)

- `bootstrap()`: the JSON adds `"synthetic": environment.synthetic()` next to the branding. It
  never adds the environment's name. `_branding()` is unchanged, so `ProfileView` and CARE's
  `care.config.ts` see nothing new.
- `bootstrap_js()`: `WARNING_SCRIPT` is appended as today; when synthetic, `SYNTHETIC_SCRIPT` is
  appended too. **No `window.__IHR_ENVIRONMENT__` global** (it had no reader) [L3].
- **No banner on the release gate's stub** [M10]: its overlay is opaque and full-screen at
  `z-index:2147483647` (`release_gate.py:169`), so nothing can be typed under it and a bar one
  z-index below it would be invisible. `bootstrap_stub` is unchanged.

### 7.3 Two bars, no shared host (Q-K158 C)

Two fixed bars at `top:0` would overlap: the IST warning and the synthetic banner. **No shared
top-bar host** [M4, R1]: the shipped IST bar is not refactored (`test_device_time.py` pins many of
its strings, the owner confirmed its behaviour in P1-T7b, and the refactor would have broken it on
production, where the host would be undefined and its catch-all `try` would hide the error).

- `SYNTHETIC_SCRIPT` draws **its own bar** at `top:0` and publishes its height in a CSS variable
  (`--ihr-synthetic-h`). It has `role="note"`, not `alert` (it is permanent), never hides, and has
  no close button; it uses `2147483646` (one below the release gate's overlay). Rules carried over
  from `device_time`: no `<` anywhere (test), no `innerHTML`/`eval`, a `try` around everything.
- The IST bar adds that offset to its `top` and to its reserve rules: a 1–2 line change in
  `device_time.py`.
- **First step of the builder: run the tests-ihr suite with the banner on**, before anything else.
  **Cap the layout fixes at about 0.5 h**; past the cap, fall back to "servers only" (dev and CI
  without the banner; Q-K158 B).

### 7.4 The console

`templatetags/ihr_environment.py` (with a new `templatetags/__init__.py`): `{% synthetic_banner %}`
renders `<div class="ihr-synthetic" lang="en">…</div><div class="ihr-synthetic" lang="hi">…</div>`
when synthetic, else nothing; **the Hindi `<div>` carries the class too**, or it renders unstyled
under `style-src 'self'` [L1]. `ihr_console/base.html` puts it as the first child of `<body>`; the
console's `STYLE` (`console/views.py:43`) gets the class. The console is on the platform host; it
does not serve `bootstrap.js`.

### 7.5 Not here: Keycloak pages, ops hosts

The Keycloak login and set-password pages do not show the banner (Q-K157 A). No custom theme
exists (`deploy/keycloak/` holds only the password block-list), no patient data is typed there,
and staff arrive from or return to a hospital page that shows it. Grafana, the status page and
`ops.<suffix>` are operator tools with no patient entry.

## 8. Tests

### 8.1 Python (tenant labels `ihr_platform ihr_tenant_settings ihr_access`, plus `ihr_platform.tests.test_restore`)

- `ihr_platform/tests/test_secure_settings.py`: the safe fixture (:87) gets
  `IHR_ENVIRONMENT=production-1`; `test_every_required_variable_must_be_set` covers it; new
  `test_environment_must_be_a_known_name` (`""`, `prod`, `production-0`, `production-01`,
  `Production-1`, `staging `, `dev` refused; `production-1`, `production-12`, `staging`, `proof`
  pass); `test_production_never_runs_on_the_proof_suffix`; the same for `watch_problems`.
- `ihr_platform/tests/test_environment.py` (new): `name()` is `dev` when unset; `synthetic()` for
  each value; dev and test settings leave `IHR_ENVIRONMENT` unset (so `db_roles.off_problems`
  stays as it is); the regex equality test across every copy (§2) [L4].
- `ihr_tenant_settings/tests/test_hospital_profile.py`: `bootstrap` JSON has `synthetic: true`
  under test settings and `false` under `override_settings(IHR_ENVIRONMENT="production-1")`, and
  never the name; `bootstrap.js` contains `ihr-synthetic` and both texts when synthetic and
  neither on production, and **on production the IST bar script is still served** [R1]; fix the
  `endswith(WARNING_SCRIPT)` assertion (`test_hospital_profile.py:261`) for the appended script;
  the existing "cannot break out of a script tag" test covers the new script.
- `ihr_tenant_settings/tests/test_device_time.py`: still passes; the IST bar applies the
  `--ihr-synthetic-h` offset. A new test file for `SYNTHETIC_SCRIPT`: no `<`, no `innerHTML`, one
  balanced function.
- Release gate: **no** stub-banner test (§7.2).
- Console: a test renders `home.html` with and without production, and the Hindi div carries the
  class.
- `ihr_platform/tests/test_restore.py`: forged packages (`_forged_restore`, :852) with a different
  environment (refused), a different suffix with the same environment (accepted with a recorded
  warning), and none recorded (accepted with the note), under `dev`.
- `scripts/test_restore_quarantine.py` and the live case: `source` carries `environment`, `suffix`
  and `instance_recorded` from a real manifest [S1].
- `ihr_access/tests/test_role_matrix`: passes with `synthetic` added to `role_matrix_keys.txt`.
- Isolation suite: unchanged routes; `anonymous_allowlist.txt` comment only.

### 8.2 Scripts (`python3 -m unittest`)

- `scripts/test_staging_environment.py` (new), bash functions extracted from `staging.sh` the way
  `test_bootstrap_static.py` extracts them:
  - `init` writes `proof` for `--local`/`--vm` and the given value for `--environment`; refuses no
    option, `--environment proof`, both options, and a bad value; production buckets are named
    `ihr-prod<n>-*`, `staging` refuses `prod` names;
  - **the dispatch table** of §4.3: each entry refuses on a `production-1` folder (as `$DIR`, then
    as `$OUT`) and passes on `staging` and `proof`; a removed table entry or call fails;
  - `env_of`/guards with no `.env` in `$DIR` and none in `$OUT`, and one row per malformed form
    (§4.1) [S4, S5];
  - `env_state_problems` reports **every** marker of §4.4 on production and none on staging
    (the earlier "six markers" count is dropped) [M6];
  - `compose()`: `ensure_environment` runs on every call and `env_state_problems` on start verbs
    only; `init --check` exit codes and messages; the `.local` upgrade of §4.5 and the
    `$VM_CACHE/staging` upgrade [Z3].
- `scripts/test_instance_check.py` (new): every row of §5.2's table, including no sentinel, `INSTANCE`
  missing, sources disagreeing, an environment without a suffix, an empty `DEPLOYED` with a
  `.env`, and a legacy `DEPLOYED`; reasons name both instances.
- `scripts/test_bootstrap_static.py` (reads `deploy.sh`, `bootstrap.sh`): the instance read comes
  before `DEPLOYING` is written and ends in `#end`; `DEPLOYED` has `environment=`; `--local-proof`
  and `proof` rules; `bootstrap.sh --instance` writes a root-owned `INSTANCE`.
- `scripts/test_dumps.py`: new manifests carry both fields under the MAC; the bucket marker
  (missing, bad MAC, other instance → `run` refuses); a foreign unauthenticated manifest gives the
  named reason and an alert but dumps continue; an authentic foreign one is a "key incident"; an
  old one stays valid; `Config` requires the instance only for `run` and `once`; every `dumper.py`
  invocation in `staging.sh` passes both values.
- `scripts/test_staging_operator.py`: `init --check` calls no wrapper (static); static wrapper
  tests still pass.
- `scripts/test_staging_surface.py`: unchanged list (no subcommand added) passes.
- `scripts/test-dumps-live.sh`: runs with `IHR_ENVIRONMENT=proof`.

### 8.3 Playwright (`apps/frontend/tests-ihr/synthetic-banner.spec.ts`, new; CI "Browser isolation")

0. First, before any other change, the whole existing tests-ihr suite is run with the banner on
   (A7: `timezoneId: "Asia/Kolkata"`, so the IST bar never shows in CI today and the banner is the
   first top bar the specs meet) [M4].
1. Anonymous `http://alpha.localhost:8080/` (the sign-in page): `#ihr-synthetic` is visible with
   both texts, and the page's first control is not covered by it (bounding boxes).
2. Signed in as a seeded user: the banner is on a patient list page; the side bar's top is below
   the banner.
3. With `timezoneId: "UTC"` in that test only: the IST bar and the banner both show, stacked (the
   IST bar offset by `--ihr-synthetic-h`), not overlapping.
4. The whole existing tests-ihr suite passes with the banner on (dev shows it, Q-K158).

### 8.4 No isolation mutation row

These are operations guards, not tenant-isolation controls, so `scripts/isolation-mutation.sh`
gets no row. The equivalent here: the §8.2 test fails if any dispatch-table entry or its
`refuse_on_production` call is removed, and the §8.1 tests fail if the banner logic flips.

### 8.5 Local proof (after the build)

On this workstation, dev stack stopped: `scripts/staging.sh local-proof` on an existing proof
folder (the §4.5 upgrade, then a green smoke with the environment line and `ihr-synthetic` in
`bootstrap.js`);
then, on a scratch copy with `IHR_ENVIRONMENT=production-1`, `mail-init --test`, `acme-init
pebble …`, `local-proof` and `up` (with a stand-in in `.env`) are refused with the ADR reference.
The VM proof (`deploy.sh --local-proof`) is optional (RAM).

## 9. Split (final; about 11.5 agent hours)

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
- The estimate stays near the earlier 11 h, against ADR-020's 4–6 h guess: cutting §6.3 and the
  top-bar host saves about 1.5 h; host identity (S2) and the bucket marker (M2) close two real
  gaps and add about 1 h; the local proof gets its own step.
- First cut if the run must shrink: the `staging`/`prod` bucket-name rule (M6, last two bullets),
  then the Playwright spec (keep the unit tests).

**Review shape:** S2 and S3 change where secrets may go; S4 changes a MAC-checked object and a
restore refusal. So: Opus high builders for S3 and S4; one Opus high review (operations and
security lens) of the whole diff; Opus high fixers for any finding on the `INSTANCE` check, the
marker or the restore precheck. No access, tenancy or audit-chain controls change, so the full
3-lens shape is not needed.

## 10. Not done

- Keycloak pages, Grafana, the status page and the ops hosts get no banner (§7.5).
- **Backup and log-archive labels** (pgBackRest annotations, §6.3 cut) and a marker for those
  stores: their stores are encrypted per instance and the repository path is per suffix. The log
  archive, audit-fingerprint and events buckets record no environment either (ADR-020 lists only
  dumps and backups). Candidate follow-up.
- No override for E3. A server changes instance only by being rebuilt.
- No new `staging.sh` subcommand; no renaming of `deploy/staging/`, `staging.sh` or `:staging`
  tags (ADR-020 §1.6).
- Alert e-mails naming their instance (ADR-020 §1.5) is not part of E1–E5.
- No web-app (CARE bundle) change and no `deploy/locale` strings.
- No shared top-bar host, no stub banner, no `__IHR_ENVIRONMENT__` global (§7).

## 11. Assumptions (each checkable)

- A2: `body_mac` covers every key of the manifest (`deploy/lib/sealer.py:105`), so new fields are
  authenticated.
- A3: Compose's `${VAR:?msg}` fails every command, `ps` and `down` included, when the variable is
  missing: `docker compose -f deploy/staging/compose.yaml config` on a fixture without it.
- A4: no server besides the local VM has been deployed (TRACKER: P1-T26 not done).
- A5: `IHR_ENVIRONMENT` is unset in `settings/local.py` and `settings/test.py`
  (`grep -n IHR_ENVIRONMENT plugins/ihr_platform/ihr_platform/settings/`).
- A6: `index.html` loads `/api/ihr/bootstrap.js` on every web-app page, sign-in and public
  appointment pages included (`apps/frontend/index.html:30`; nginx serves one `index.html`).
- A7: `playwright.ihr.config.ts:26` sets `timezoneId: "Asia/Kolkata"`, so the IST bar never shows
  in CI today and the synthetic banner is the first top bar the existing specs meet.
- A8: `role_matrix_keys.txt:68` records the bootstrap JSON's keys for the restricted roles.
- A9: every console page extends `ihr_console/base.html`.
- A10 (corrected [S1]): the restore package's `source` does **not** carry the manifest
  (`deploy/restore/quarantine.py:204-208` builds it from `kind`, `dump_at`, `dump`, `kind_of_dump`
  and `manifest_sha256` only). §6.2 makes the quarantine copy `environment`, `suffix` and
  `instance_recorded` into it.

## 12. Decisions (Q-K150…Q-K159; an unattended run auto-accepts the taken option)

- **Q-K150 Environment names.** A (taken): `production-<n>` (1–999), `staging`, `proof` on
  servers; dev/tests unset (reported `dev`); P1-T26's rented VM is `staging`; the local proof and
  the local VM are `proof`. B: free-form names with a `production-` prefix rule. C: `dev` as a
  server value too. A closed list makes a typo a refusal, and `proof` tells a throwaway from a
  synthetic server.
- **Q-K151 How `init` learns it.** A (taken, amended): `--local`/`--vm` mean `proof` (`init --vm`
  writes `proof` too); otherwise `--environment` is required; production folders get
  `ihr-prod<n>-*` bucket names. B: default `staging` when not given. A silent default is the bug
  ADR-020 found.
- **Q-K152 Folders and servers from before E1.** A (taken, amended): the upgrade runs in
  `compose()` (every command); proof folders (`.local`) are upgraded with a printed line; the VM
  cache upgrades by exact path (`$VM_CACHE/staging`) and suffix, not by `.local`; any other folder
  is refused with the one-line fix. B: write `staging` into any folder lacking it (could label a
  real server synthetic).
- **Q-K153 E2's reach.** A (taken, amended): one dispatch table of stand-in commands plus the state
  check on every Compose start verb, `init --check`, `release` and `deploy.sh`; parsing fails
  closed. B: commands only (a copied or hand-edited `.env` bypasses it).
- **Q-K154 E3 rules.** **C (taken; new):** A plus host identity. `bootstrap.sh --instance` writes
  a root-owned `INSTANCE`; a missing or different one refuses before anything is sent; no "new
  server ok" row; no override; `proof` folders only with `--local-proof`; a production folder
  inside the repository refused. A: the §5.2 table alone (a fresh server would take production's
  secrets). B: A with `--replace-instance`.
- **Q-K155 Dumps and backups older than P1-T32.** A (taken): accepted, with "environment not
  recorded" in the report; the key and chain checks still apply. Backups drop out of P1-T32 (§6.3
  cut). B: refused.
- **Q-K156 Another instance's objects in this instance's bucket.** **C (taken; new):**
  marker-based. An authentic `instance.json` decides; a missing or foreign marker refuses `run`; a
  stray foreign object is a `check` problem and an alert, and dumps continue. A: refuse every dump
  on any foreign manifest (one stray object halts all nightly dumps). B: report only.
- **Q-K157 Where the banner shows and what the bootstrap says.** A (taken, amended): every web-app
  page (sign-in, public pages) and the operator console; **no stub banner** (the overlay covers
  it); **no `__IHR_ENVIRONMENT__` global**; not Keycloak or ops hosts; the public JSON says
  `synthetic: true|false`, never the instance name. B: A plus a Keycloak theme. C: the web app
  only.
- **Q-K158 Dev and layout.** **C (taken; new):** dev and CI show the banner; no shared host (its
  own bar plus a CSS-variable offset for the IST bar); fallback B (servers only) after a 0.5 h
  layout cap. A: dev and CI show it through one shared top-bar host. B: servers only, banner at
  the bottom, IST bar untouched.
- **Q-K159 Restore across suffixes (new).** A (taken): the environment must match; a different
  suffix warns and is recorded in the restore report (disaster recovery to a new domain). B:
  refuse any suffix difference (a rebuilt production-1 on a new domain could not restore its own
  dumps).
