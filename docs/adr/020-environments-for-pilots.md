# ADR-020: Environments for pilots: production-1, synthetic staging and the drill copy

- **Status:** **Proposed.** The choices below were auto-accepted by Claude, 2026-11-22, awaiting
  owner confirmation (Q-K132, Q-K133). Nothing here is built yet: §3 names the checks that exist
  today and the ones still to build.
- **Date:** 2026-11-22
- **Decided by:** Claude, in task P1-T28, from Phase 1 closing review finding PR1-2
  (`docs/reviews/phase-1-closing-review.md`). The owner decides.
- **Amends:** ADR-003 (Amendment 3: the production row and the scale-out trigger). Builds on
  ADR-011 (provider-neutral hosting), ADR-007 (RPO ≤ 15 minutes, RTO ≤ 4 hours before real data),
  ADR-019 (the 8 GB load test) and the P1-T1c scale results (`docs/reviews/p1-t1c/scale-results.md`).

## Context

Four statements did not fit together:

- CLAUDE.md: "No real patient data outside production".
- ADR-003: production "starts as Compose on a VM" and moves to k3s/RKE2 + CloudNativePG "at about
  10 tenants or when HA is needed (P9-T7)".
- PLAN §3.2: production is "managed Postgres or CloudNativePG on k3s/RKE2". ADR-011 has since
  ruled out managed databases without a new ADR.
- Everything built since P1-T8 is called "staging" (`deploy/staging/`, `scripts/staging.sh`), and
  every go-live gate assumes the first pilot runs on that stack.

So, as written, a pilot on "staging" breaks the no-real-data rule. Restore drills of real dumps
have no defined place. And P9-T7 would rebuild the backup, dump, role and release tooling at a
point nobody has measured or costed.

## Decision

### 1. The first pilot runs on "production-1": the same Compose stack, as its own instance

**Options:**

- **A ⭐ (taken).** The existing Compose stack (`deploy/staging/compose.yaml`, `scripts/staging.sh`,
  `scripts/deploy.sh`) deployed once more as a separate instance named **production-1**. It gets
  its own VM, its own `init` (so its own secrets), its own buckets and bucket keys, its own
  Keycloak, its own acme-dns and its own domain suffix. Hospitals live at `<slug>.app.amhcloud.in`
  (ADR-011 §4). It never shares a VM, database, bucket, key, Keycloak, mail token or alert inbox
  label with any synthetic environment. It is built fresh: the P1-T26 dress-rehearsal VM, its
  buckets and its keys are never reused for it.
- B. Take the one staging server as it is and start putting real hospitals on it. This is the
  cheapest option, but it leaves no place to rehearse a release or a drill without real data
  nearby, and synthetic demo hospitals would share a server with real ones.
- C. Build P9-T7 (k3s/RKE2 + CloudNativePG) or a managed database first. This rebuilds Phase 1's
  whole ops layer (§4) before the first pilot, and ADR-011 rules out managed databases.

**Why A:** every go-live gate (P1-T18 to P1-T23, P1-T26) has been proven on this stack. A second
instance needs no new code (below). The only real cost is a second set of secrets to keep apart.

**What the code supports today.** A second instance is mostly configuration:

- `scripts/staging.sh init <suffix>` writes `.env` and `secrets/` into `IHR_STAGING_OUT` (default
  `deploy/staging/`). Every public host follows from the suffix: `<slug>.<suffix>`,
  `console.<suffix>` and `auth.<suffix>`.
- `scripts/deploy.sh --env-dir <folder> --host ihr@<address>` sends that folder's `.env` and
  `secrets/` to the server.
- The remote backup, dump and log-archive buckets are arguments, so each instance names its own:
  `backup-init <endpoint> <bucket> <region>`, `dumps-init …` and `logs-archive-init …`.
- `acme-init <ca> <acme-zone> <ns-name>` takes its own zone names.
- On its own VM, the fixed Compose project name `indiehr-staging` and its volume names do not
  collide with anything.

**What must change (names only; no code in this task):**

1. **Environment name.** `init` writes no `IHR_ENVIRONMENT`. Compose and the settings default it
   to `staging`, and `settings/deployment.py` calls it "A label only". For production-1, add
   `IHR_ENVIRONMENT=production-1` to its `.env` by hand; this works today. §3 makes the value
   required and enforced.
2. **Patient and picture buckets.** `init` writes `FILE_UPLOAD_BUCKET=ihr-staging-files` and
   `FACILITY_S3_BUCKET=ihr-staging-facility` into `app.env`. On provider storage these are set on
   the server anyway, but production-1 must use its own names, e.g. `ihr-prod1-files` and
   `ihr-prod1-pictures`. Bucket names are global on some providers, and a production bucket must
   not carry the word "staging".
3. **Bucket names for the other stores.** The SeaweedFS defaults `ihr-backups`, `ihr-dumps`,
   `ihr-log-archive` and `ihr-audit-anchors` are fine on a VM of their own. On provider storage,
   every instance names its own, with the instance in the name (e.g. `ihr-prod1-backups`). The
   audit-fingerprint bucket stays in a separate account or project (ADR-011 §3), one per
   instance.
4. **Settings folder on the deploying machine.** production-1's `.env` and `secrets/` live in
   their own mode-700 folder **outside** the repository checkout, e.g.
   `~/indiehr-envs/production-1/`. They are used only with `deploy.sh --env-dir` and with
   `IHR_STAGING_OUT=` for `db-split-secrets` and `s3-split-keys`. `deploy/staging/` stays the
   synthetic instance's folder. Every production-1 secret also goes into the owner's password
   manager, like the dump and archive keys already do.
5. **Certificates and mail.** production-1 uses `acme-init letsencrypt acme.amhcloud.in
   acme-ns.amhcloud.in`. A synthetic VM uses other names (e.g. suffix `stg.amhcloud.in`, zone
   `acme-stg.amhcloud.in`). Each instance has its own ZeptoMail token, and its alert e-mails say
   which instance sent them.
6. **Names that stay.** `deploy/staging/`, `staging.sh`, the project name `indiehr-staging` and
   the `:staging` image tags stay as they are. Renaming them would touch hundreds of references
   and the runbooks. From this ADR on, "the staging stack" means the Compose stack, not the
   environment. The environment is whatever `IHR_ENVIRONMENT` says.

### 2. Synthetic staging lives apart from production-1, and real dumps are drilled inside production-1

**Options for synthetic staging (Q-K133):**

- **A ⭐ (taken).** For now, this workstation: `scripts/staging.sh local-proof` and the throwaway
  local VM (`scripts/deploy.sh --local-proof`). P1-T26's rented VM is synthetic staging while it
  exists, and is destroyed afterwards. A paid, always-on staging VM comes only when the owner
  chooses to pay for one, for example when pilot staff need a training URL. It would cost about
  ₹4,780 a month at ADR-011's E2E price, or less if P1-T26 shows the small settings fit.
- B. A second small paid VM from the first pilot on, always on, with its own suffix
  (`<slug>.stg.amhcloud.in`).
- C. The restore-drill copy. **Refused:** that copy is production data (below), so it can never
  be staging.

**Rules for synthetic staging, wherever it runs:**

- Only synthetic data: seeded demo hospitals, bench databases and made-up patients.
- It never receives real data. That includes production-1's pgBackRest backups, its nightly
  hospital dumps, its log archive, its audit exports and any copy or extract of them.
- The synthetic instance (its settings folder, containers and volumes) never holds a production-1
  key, so it cannot decrypt one of those objects even by mistake. The deploying machine may keep
  production-1's folder (§1.4), outside the repository and mode 700, and never passes it to
  `local-proof`, `--local-proof` or a synthetic `--env-dir`.
- Release rehearsals and drills of the tooling itself run here first: `release`, `rollback`,
  `restore-drill`, `hospital-restore-drill` on its own synthetic dumps, and the `*-probe`
  commands.

**Where restore drills of real dumps run: production-1's own restore-drill copy.** The P1-T22b
drill already works this way:

- `restore-drill` restores pgBackRest into the container `ihr-restore-drill`, beside the live
  database on the same VM.
- `hospital-restore-drill` restores the newest real dump into that copy only. `--drill` refuses
  any database whose `cluster_name` is not `ihr-restore-drill`, and the one-off runs with
  `ihr_platform.settings.drill`.
- The copy is removed afterwards unless `--keep`.

So real data never leaves production-1, not even for a drill. Here "production-1" means its VM and
its own buckets and accounts (the backup region, the audit-anchor account). Two consequences:

- production-1's disk must hold the live database, the drill copy and one decrypted dump at the
  same time. `restore-drill` already refuses to start without room.
- The quarterly live drill (`deploy/staging/README.md`, "Restoring one hospital") uses a synthetic
  `drill` hospital inside production-1. Synthetic data inside production is allowed; real data
  outside it is not.

### 3. The no-real-data rule, per environment

| Environment | Real patient data? | What may come in | What must never come in |
|---|---|---|---|
| **local** (`care-dev.sh`, tests, CI, `local-proof`, the local VM) | Never | Seeds (`ihr_seed_demo`, `ihr_keycloak_demo_users`), bench databases (`bench_*`), synthetic fixtures | Any production-1 backup, dump, log, export or key, except the production-1 secrets folder that deploys it (§1.4), which holds keys and no data |
| **synthetic staging** (Q-K133) | Never | Synthetic hospitals made through provisioning; its own dumps and backups | Anything from production-1, real or derived, including restored dumps. Its instance never holds a production-1 key; the deploying machine may keep production-1's secrets folder (§1.4), which holds keys and no data, and never passes it to a synthetic `--env-dir` |
| **production-1** | Yes | Real hospitals; marked synthetic hospitals (the quarterly `drill` hospital, a training hospital if the owner wants one) | Seed commands; test stand-ins (Pebble, mail and storage stand-ins); test reference packs |
| **drill copy** (`ihr-restore-drill` on production-1) | Yes, the same as production-1 | Production-1's own backups and dumps | It never leaves production-1's VM and is removed after each drill |

On this workstation, local and synthetic staging are the same environment until a synthetic VM
exists.

Operator commands that read real data (`dumps-get`, `logs-archive-get`, `audit-supersede export`,
`hospital-restore`) run on production-1, and their output stays there. Real data may leave
production-1 only to the hospital it belongs to (its own audit export), to the data principal under
DPDP, or under an owner-approved legal or CERT-In request, never to another environment.

**Already enforced (checked 2026-11-22):**

- `settings/secure.py` refuses `DEBUG` on every server. `ihr_seed_demo`,
  `ihr_keycloak_demo_users` and `seeding.make_waiting_pack_item` refuse to run without `DEBUG`,
  so no server, production or synthetic, can run the seeds.
- `secure.py` also refuses `IHR_REFDATA_TEST_KINDS` (test packs) on servers.
- `ihr_bench_scale` refuses any database not named `bench_*`.
- `hospital-restore --drill` refuses any database that is not the drill copy.
- A dump cannot move between instances:
  - each instance has its own dump key (`secrets/dumps.env`), so another instance cannot read
    the dump;
  - the restore pre-check refuses a dump whose audit head is not a seal of the live chain, and
    two instances never share a chain.

**Not yet enforced (to build in a follow-up task; the owner numbers and schedules it, about 4 to
6 agent hours):**

- E1. `IHR_ENVIRONMENT` becomes required on servers (`secure.py`), with the values
  `production-<n>`, `staging` or `proof`. `init` writes it, and `smoke` prints it.
- E2. When `IHR_ENVIRONMENT` is `production-*`, `staging.sh` refuses the proof stand-ins:
  `local-proof`, `acme-proof`, `acme-init pebble|letsencrypt-staging`, `mail-init --test`,
  `monitoring-init --test`, and the `--local` variants of the init commands.
- E3. `deploy.sh` refuses to send a settings folder whose `IHR_ENVIRONMENT` or suffix differs from
  what the server last ran (`/srv/indiehr/DEPLOYED`). This stops the synthetic folder from being
  deployed over production-1, and the reverse.
- E4. Dump manifests and backup labels record the environment, so a refusal to cross instances
  names the reason instead of failing on keys or the chain.
- E5. Any non-production instance shows a fixed "Synthetic data only: do not enter real patients"
  banner. It comes from the public bootstrap, so no login is needed to see it. A person typing a
  real patient into a demo server is the likeliest way this rule breaks.

### 4. When P9-T7 is needed: measured limits, not "about 10 tenants"

Growth gets handled in three steps, and only the last one re-platforms:

1. **A bigger VM.** It needs no code: the same stack and new memory settings, re-checked with the
   ADR-019 static test.
2. **More Compose cells.** production-2 and later cells are each a full instance with the same
   tooling. The open costs of this step are listed under "What stays open".
3. **P9-T7: k3s/RKE2 + CloudNativePG.** This is needed only for high availability or for too many
   cells to run by hand.

| Limit | Measured by | Threshold | Response |
|---|---|---|---|
| L1 Memory and CPU | P1-T26 load test (baseline: memory ceilings of about 7.1 GB (core) plus about 3 GB (sidecars) on 8 GB; real use is measured in P1-T26); host metrics (P1-T9a) | Peak RAM use (without page cache) above 85 % of the VM on 3 days in a week, any OOM kill, or CPU above 70 % for an hour at peak | Step 1, then step 2 |
| L2 Hospitals per cell | P1-T1c (cap 100; the dump-time cliff sits at 125 to 150 on the dev machine); re-measured in P1-T26 and yearly on production-1 | The cap in force (100 until re-measured) | Step 2 |
| L3 Whole-server restore time | Monthly `restore-drill` on production-1 (pgBackRest, the whole database) | Above 2 hours, half the 4-hour RTO (ADR-007) | Step 2 (smaller cells restore faster) |
| L4 Nightly dumps | `ihr-dump` timings (P1-T22a, DumpDrillOverdue and dump alerts) | All hospitals not dumped by 06:00 IST (2.5 hours after the 03:30 start) | Step 1, then step 2 |
| L5 Release window | `ReleaseWindowOverrun` (P1-T18b) | The window overruns on 2 releases in a row, even with `--parallel` raised | Step 2 |
| L6 Availability | Contracts and law | A signed contract or rule needs failover faster than the measured L3 restore time, or uptime one VM cannot give (provider SLA 99.9 to 99.95 %, ADR-011) | **Step 3: P9-T7** |
| L7 Cells to run by hand | The count of production cells | More than 4 cells, or operator chores (P1-T30 `staging.sh due`) above about 1 day a week | **Step 3: P9-T7** |

**What P9-T7 would re-platform.** This is a rough estimate by Claude and is not measured. The
Compose-specific ops layer is about 5,400 lines of `scripts/staging.sh`, 400 of `deploy.sh`,
3,500 of Python under `deploy/` (dumper, quarantine, shared sealer and locked-bucket code, log
archive), 1,400 of Compose files and an 1,800-line runbook. Under k3s and CloudNativePG:

- **pgBackRest** (`backup-init`, `backup-now`, `backup-status`, `restore-drill`,
  `restore --target`, `rollback --restore`, `hospital-restore --from-pitr`) moves to
  CloudNativePG's own backup and point-in-time recovery, with a different drill and
  restore-point model.
- **Database roles** (`db_roles_apply`, `db-roles --assert`, `pg_su`, `server-settings.txt`,
  `db_server_check`) would fight the operator's managed roles and parameters until they are
  rewritten as Cluster spec.
- **The dumper and single-hospital restore** (a no-network quarantine container, logged one-offs,
  exit code 3 "restart the app containers") become CronJobs, Jobs and NetworkPolicies.
- **Release and rollback** (hold, stop workers, drain, seal, restore point, start, smoke, image
  retention, release log) become rollout logic.
- **Keys and secrets:** client keys in Docker volumes and env-file secrets move to cluster
  secrets.
- **Routing, logs and monitoring:** Traefik's file routes, Fluent Bit reading the journal, the
  log archive, the audit watch and monitoring become cluster resources.
- **Proofs:** every live proof and drill has to be redone.

**Order of size:** 80 to 150 agent hours, plus reviews, plus a migration of live hospitals with
downtime. So P9-T7 waits until L6 or L7 is reached.

### 5. Consequences

- The no-real-data rule holds with the pilot on the stack that passed the gates. "Production" now
  means an instance whose `IHR_ENVIRONMENT` starts with `production-`, not a different technology.
- The owner keeps two sets of secrets apart: the synthetic folder in the repository's
  `deploy/staging/` (git-ignored), and production-1's folder outside it. Until E3 is built, a
  deploy with the wrong `--env-dir` is stopped only by the operator's care.
- Real-data restore drills need production-1's disk sized for the drill copy (about 2.5 times the
  database, plus one decrypted dump).
- P9-T7 is no longer tied to a tenant count. A bigger VM and further Compose cells come first.
- ADR-003's production row and PLAN §3.2 now point here. ADR-003's history is kept and amended,
  not rewritten.

### What stays open for the owner

- **Provider for production-1:** E2E Delhi NCR with backups to Chennai (ADR-011), unless the owner
  switches to the documented AWS Mumbai/Hyderabad alternative.
- **VM size:** 8 or 16 GB, decided by P1-T26's measurements. The disk must also hold the drill
  copy.
- **When:** production-1 is created only after the go-live gates pass (P1-T26, P1-T27, this ADR,
  and the ADR-007 RPO/RTO gate, P4-T7), and only when a pilot hospital signs. Paid resources are
  an owner gate.
- **Whether a paid synthetic staging VM runs alongside** (Q-K133 B), and from when.
- **Cells under one suffix (before production-2):**
  - The single wildcard record `*.app.amhcloud.in` points at one VM. A second cell needs
    per-hospital DNS records or a front router.
  - Slugs must stay unique across cells: today they are checked only inside one database.
  - Moving a hospital between cells needs a new tool, because a dump cannot cross instances (§3).
- **The follow-up task for E1 to E5,** and its number.
