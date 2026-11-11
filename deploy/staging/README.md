# Staging stack (P1-T8a)

One VM in an Indian region (provider-neutral, ADR-011), Docker Compose, Traefik in front
(ADR-003). Everything runs through `scripts/staging.sh`; the server itself is set up by
`deploy/host/bootstrap.sh` and the stack reaches it through `scripts/deploy.sh` (P1-T8b1).

| Command | What it does |
|---|---|
| `scripts/staging.sh init <suffix>` | Writes `.env` (site settings, image digests) and `secrets/*.env` (mode 600, git-ignored). Refuses to overwrite. `<suffix>`: hospitals live at `<slug>.<suffix>` |
| `scripts/staging.sh up` | Database, Redis, migrations, web app, Celery, Keycloak, Traefik; then the Keycloak realms and the two workers' narrow accounts |
| `scripts/staging.sh smoke` | 20 checks through Traefik: what is public and what is not, TLS redirect, HSTS, token issuer, production mode, no query strings in logs |
| `scripts/staging.sh manage <args>` | One-off `manage.py` (never logged; no Keycloak admin credential) |
| `scripts/staging.sh local-proof` | All of the above on this machine (127.0.0.1, self-signed certificate) |
| `scripts/staging.sh acme-init …` etc. | The wildcard certificate: see below |
| `scripts/deploy.sh --host ihr@<address>` | From this machine to a bootstrapped server: builds our two images (web app from the CI bundle), sends them with `docker save \| ssh docker load` (skipped when the server has that build), sends the tracked stack files and, as a tar stream over SSH, `.env` + `secrets/` (mode 600), then runs `staging.sh up` and `smoke` there, removes the images it replaced (`docker image prune`) and writes `/srv/indiehr/DEPLOYED` (commit and dirty flag as labelled on the images, image IDs, web bundle id). `/srv/indiehr/DEPLOYING` is written first and removed only on success: if it is still there, the last deploy failed part-way. The backend image is built from the committed files (`git archive`), never from git-ignored ones. Refuses a dirty git tree without `--allow-dirty`, images built from another commit with `--no-build` unless `--allow-dirty`, and a server whose host key is not yet in `known_hosts` |
| `scripts/deploy.sh --local-proof` | The same into the throwaway VM of `scripts/local-vm.sh` (settings from `staging.sh init app.ihr-proof.in --vm`, kept in `~/.cache/indiehr/vm/staging`); runs `staging.sh local-proof` there (hospital `alpha` + smoke). Runbook: `deploy/host/README.md` |

**On the server** the layout mirrors the repository (compose.yaml uses `../keycloak` and
`../docker`): `/srv/indiehr/{deploy/staging,deploy/keycloak,deploy/docker,scripts/staging.sh}`.
`staging.sh` works there without a git checkout. Smoke checks on a server without DNS yet:
`IHR_SMOKE_RESOLVE=127.0.0.1 scripts/staging.sh smoke`.

**Public addresses** (`traefik/routes.yml`, a fixed file: Traefik has no Docker socket, runs as
nobody with no capabilities):

- `<slug>.<suffix>`: the web app; `/api/` goes to the backend with the host, which picks the hospital
- `console.<suffix>`: `/ops/` and `/static/` only (operator console)
- `auth.<suffix>`: the `indiehr` and `indiehr-ops` realms only
- everything else on console/auth: nothing. The back-channel logout, health checks, Keycloak's
  admin console and master realm stay internal (admin console: SSH tunnel to `127.0.0.1:8081`).

**Secrets per container:** the web app and Celery get `app.env` (Django key, signing keys,
database and Redis addresses); the web app also the audit bucket's read-only key, the Celery
worker its add-only key; each Keycloak worker only its own narrow account; Keycloak its database
password and first-start admin, which `staging.sh` passes only to one-off commands.

**Settings** (`ihr_platform.settings.deployment`): the server refuses to start, naming every
problem, without its own secret key, signing keys, hosts, https addresses, secure cookies, HSTS,
production mode, or with CARE's terminology default or Sentry (`settings/secure.py`). It also
refuses any `TIME_ZONE` other than `Asia/Kolkata`, or `USE_TZ` off (P1-T7b).

## Staff computers must use India Standard Time (P1-T7b)

The web app shows and takes every date and time in the computer's own time zone. A computer on
another zone (a laptop set up abroad, a cloud desktop in a non-India region) shows wrong times
and saves wrong ones. So every staff computer must be set to **India Standard Time**:

| System | How (as an administrator) |
|---|---|
| Windows | `tzutil /s "India Standard Time"` (or Settings > Time & language > Date & time > Time zone "(UTC+05:30) Chennai, Kolkata, Mumbai, New Delhi"); Group Policy or Intune for many machines |
| Linux (kiosk, thin client, cloud desktop) | `sudo timedatectl set-timezone Asia/Kolkata`; for one browser only, start it with `TZ=Asia/Kolkata` |
| ChromeOS (managed) | Admin console: Devices > Settings > `SystemTimezone` = `Asia/Kolkata`, automatic detection off |
| macOS | `sudo systemsetup -settimezone Asia/Kolkata` |

Check it: open the hospital's address. On a computer that is not on IST a red bar (English and
Hindi) stays at the top of every page, naming the computer's zone, until the zone is fixed; it
re-checks every minute. Work is never blocked. The audit trail notes the computer's zone (`tz`
in the entry's context, "device_tz" in the viewer and the CSV export) on every request and changed row made from a
computer that is not on IST; a zone that only shares the offset (Asia/Colombo) also counts as
not IST. Cause: `/api/ihr/bootstrap.js` (`ihr_tenant_settings/device_time.py`) and the
`X-IHR-TZ` header the app sends to its own API.

## Wildcard certificate (P1-T8b2)

One certificate covers `<suffix>` and `*.<suffix>`: every hospital, `console.` and `auth.`.
Traefik gets it from Let's Encrypt with a DNS check, and renews it 30 days before it expires
(90-day certificates now, 64 days from February 2027, 45 days from February 2028: all fine).
The check's TXT value goes into **our own acme-dns** on the same server, which answers on port 53
for one small zone only. The registrar keeps three records and never needs an API key; any
registrar works, and moving providers changes only the A records.

| Command | What it does |
|---|---|
| `scripts/staging.sh acme-init <ca> <acme-zone> <ns-name>` | Adds the settings to `.env` (once). `<ca>`: `letsencrypt`, `letsencrypt-staging` (rehearsal, untrusted certificates) or `pebble` (proof). For amhcloud.in: `acme-init letsencrypt acme.amhcloud.in acme-ns.amhcloud.in` |
| `scripts/staging.sh up` | Also starts acme-dns, registers its one account the first time and prints the records for the registrar |
| `scripts/staging.sh acme-register` | Prints the records again (registers only if there is no account) and checks that acme-dns still accepts Traefik's key. `--reset`: sets the old account aside and registers a new one (then change the CNAME) |
| `scripts/staging.sh cert [--wait N]` | The certificate served now: issuer, names, expiry. Fails while Traefik serves its placeholder or a certificate from another CA than `.env` names |
| `scripts/staging.sh acme-retry` | Restarts Traefik so it asks again (after the records exist), waits up to 5 minutes |
| `scripts/staging.sh acme-new-cert` | After changing the CA in `.env`: sets the stored certificate and Let's Encrypt account aside (keeps the acme-dns account), restarts Traefik, waits for the new certificate |
| `scripts/staging.sh acme-proof` | The whole path on this machine with Pebble and CoreDNS (about 8 minutes, renewal included). Also CI job "Certificate proof" |

**First certificate on a new server** (owner at the registrar; about 30 minutes):

1. Rehearse first: `acme-init letsencrypt-staging …`, deploy, records, `acme-retry`, `cert`.
   Then switch: change `IHR_ACME=letsencrypt` and
   `IHR_ACME_CA_SERVER=https://acme-v02.api.letsencrypt.org/directory` in `.env`, deploy, and on
   the server run `scripts/staging.sh acme-new-cert`. (Traefik would otherwise keep serving the
   untrusted rehearsal certificate until it is 30 days from expiry; `cert` flags that.)
2. At the registrar of `amhcloud.in` (TTL 300 while setting up):

   | Name | Type | Value |
   |---|---|---|
   | `acme-ns.amhcloud.in` | A | the server's public IPv4 address |
   | `acme.amhcloud.in` | NS | `acme-ns.amhcloud.in` |
   | `_acme-challenge.app.amhcloud.in` | CNAME | `<account>.acme.amhcloud.in` (printed by `up` / `acme-register`) |
   | `*.app.amhcloud.in` | A | the server's public IPv4 address |
   | `amhcloud.in` (optional, recommended) | CAA | `0 issue "letsencrypt.org"` |

3. Check from this machine: `dig +short TXT _acme-challenge.app.amhcloud.in` shows a value
   (after the first attempt) and `dig @<server address> +short NS acme.amhcloud.in` answers.
4. `scripts/staging.sh acme-retry`, then `scripts/staging.sh cert`. If it still has no
   certificate, the server's resolver may remember "no such record" from before the CNAME
   existed (up to the zone's negative-cache time, often an hour): wait, then `acme-retry` again.

**Why Let's Encrypt's limits are safe:** Traefik checks that the TXT value is visible at our
acme-dns itself before it tells Let's Encrypt to look. While a record is missing it gives up on its
own and asks for nothing (the proof shows this: the order is dropped, no failed check). The limits
that matter are 5 failed checks per name per hour and 5 identical certificates per week.

**Security:** acme-dns runs from our own image (pinned source, distroless, not root, read-only,
no capabilities), answers only for its zone (never a resolver), and its HTTP API listens only on
its fixed address on an internal network shared with Traefik (`IHR_ACME_API_IP`). Registration is open only for the moment
`acme-register` needs it; the account accepts updates only from that internal network. Its key
lives only in Traefik's volume (`/data/acme-dns-accounts.json`, mode 600, Traefik's user) and is
never printed or logged. The firewall lets 53 (udp and tcp) through to the container
(`deploy/host/bootstrap.sh`).

**Keep these two volumes** (backups, P1-T8c): `traefik-data` (certificate, Let's Encrypt account,
acme-dns key) and `acmedns-data` (acme-dns's side of the account). Losing either means a new
acme-dns account (`acme-register --reset`) and a new CNAME at the registrar before the next
certificate; `up` notices when acme-dns no longer accepts Traefik's key. Losing
`traefik-data` also loses the certificate itself: browsers then get Traefik's placeholder (an
error page) until a new one is issued, so restore it rather than start over.

## Files on the server: SeaweedFS (P1-T8b3)

CARE's uploads and hospital pictures, and the audit fingerprints, can live on the server itself
in SeaweedFS (free; uses the server's disk, at most `IHR_S3_MAX_GB`, default 20 GB, so it can
never fill the database's disk). `local-proof` always uses SeaweedFS.

**A provider's bucket instead** (E2E, AWS), now or later, changes settings only:
- `.env`: remove the `IHR_S3*` lines; set `IHR_STORAGE_ORIGIN` to the provider's address (the
  web app's CSP allows only that one);
- `secrets/app.env`: `BUCKET_ENDPOINT` and `BUCKET_EXTERNAL_ENDPOINT` (both the provider's
  address), `BUCKET_KEY`, `BUCKET_SECRET`, `BUCKET_REGION`, the two bucket names; the buckets need
  the same CORS (`https://*.<suffix>`) and the facility bucket the public-read policy
  (`deploy/s3/setup_buckets.py` does both against any S3 endpoint);
- `secrets/anchor-read.env` / `anchor-write.env`: `IHR_AUDIT_ANCHOR_ENDPOINT` (empty for AWS),
  `_BUCKET`, the two keys;
- copy the files across first; keep the old `s3data` volume until its fingerprints are copied
  or no longer needed (their lock cannot be lifted).

| Command | What it does |
|---|---|
| `scripts/staging.sh s3-init` | Once, after `init`: makes the four keys (storage admin, CARE, fingerprint writer, fingerprint reader) in `secrets/s3-gateway.env`, gives CARE and the two fingerprint files their one key each, and adds the settings to `.env` |
| `scripts/staging.sh up` | Also starts SeaweedFS and sets up the buckets (idempotent) |
| `scripts/staging.sh s3-check` | 38 live checks: each key does what it must and nothing more, the lock holds against every key including the admin, anonymous and browser access through `files.<suffix>` |

**Buckets:** `FILE_UPLOAD_BUCKET` (private: the browser uploads and downloads with short-lived
signed links), `FACILITY_S3_BUCKET` (anyone may read a picture, nobody may list or write), and
`ihr-audit-anchors` (Object Lock **COMPLIANCE for 10 years**: no key, not even the storage admin,
can delete or shorten a fingerprint; the verifier's production checks pass).

**Addresses:** browsers use `https://files.<suffix>` (covered by the wildcard certificate),
object paths of the two CARE buckets only; everything else there, including the fingerprint
bucket and every bucket-level call, goes nowhere. The web app and Celery use `http://s3:8333`
inside the stack. `files` is a reserved hospital slug.

**Why two containers:** SeaweedFS's own internal parts (master, volume server, filer) have no
access control, and the filer can delete a locked bucket underneath S3 (tried: one request removed
it). They listen on 127.0.0.1 only; the S3 gateway shares their network namespace and is the
only thing other containers can reach.

**Limits to know:** COMPLIANCE stops every S3 key, but not someone with root on the server, who
can delete the Docker volume. A stolen fingerprint-writer key can fill the bucket with junk that
no key can remove for 10 years; the disk cap keeps that away from the database, and the clean-up
is a root-only delete of the junk paths through the filer inside the `s3` container.
Uploads: up to 100 MB (as CARE's web app allows; Traefik refuses bigger ones) and 15 minutes. That is why production keeps fingerprints in a provider's bucket
in a separate account (ADR-011 §3); on a staging server this bucket proves the setup. Back up the
`s3data` volume with the database (P1-T8c).

## Mail: Zoho ZeptoMail (P1-T8b4)

Invitations (Django) and Keycloak's setup e-mails go through **Zoho ZeptoMail**, India data
centre (owner, 09-11-2026, Q-E10: written confirmation of India-only storage before go-live).
The server refuses to start without real mail settings (`settings/secure.py`): no mail host,
no TLS, a localhost or upstream sender, or no Keycloak mail server.

| Command | What it does |
|---|---|
| `scripts/staging.sh mail-init no-reply@amhcloud.in` | Once: asks for the ZeptoMail SMTP password (the agent's Send Mail token; typed, never on the command line) and writes the settings: `smtp.zeptomail.in:587`, STARTTLS, user `emailapikey`. `--host` / `--user` if ZeptoMail's dashboard says otherwise (port 587 only: everything uses STARTTLS) |
| `scripts/staging.sh mail-init --test` | A stand-in instead (automatic in `local-proof`): Mailpit requiring STARTTLS and the password, with a throwaway CA; nothing leaves the server |
| `scripts/staging.sh mail-check you@example.com` | Django sends a test e-mail (with the stand-in, it is read back) |

**Owner, before go-live:** a ZeptoMail account at zoho.in; a Mail Agent; verify the sending
domain (ZeptoMail gives the DKIM TXT and a bounce CNAME; add a DMARC record, start with
`p=none`); copy the agent's SMTP password. E-mails carry only a hospital's name and a one-time
link, never patient data.

**Where the password goes:** `secrets/app.env` (Django needs it) and `secrets/mail.env`, which
reaches only the one-off Keycloak sync (`ihr_keycloak_sync --realm`), which stores it in
Keycloak's realm. Nowhere else, and never in a log.

**Proven** (local staging stack with the stand-in): Django's test e-mail arrives over STARTTLS
with the password and a checked certificate; a wrong password is refused (535); a server whose
certificate is not trusted is refused; Keycloak delivered the first admin's setup e-mail when a
hospital was provisioned; the password in no container's log.

## Keycloak in production (P1-T8d1)

| What | How |
|---|---|
| Image | Our pre-built Keycloak 26.8.2 (`deploy/docker/keycloak.Dockerfile`, built and shipped by `deploy.sh`): Postgres, health, metrics and the switched-off features are fixed at build time, and it starts with `start --optimized` (no rebuild at start; a runtime setting that contradicts the build stops the start) |
| One server | `KC_CACHE=local`: no cluster discovery through the database, so a restart after a crash or power cut no longer reports "split-brain … should not be serving requests" for up to a minute. **Never run a second Keycloak container on the same database** (their caches would not see each other); `up` replaces the container, it never runs two |
| Ready | The health check needs HTTP 200 from `/health/ready` (every check, incl. the database), so `up` waits until Keycloak has really finished starting |
| Client addresses | Traefik reaches Keycloak over a network of their own (`kcproxy`, `IHR_PROXY_NET`, default 10.203.0.0/28; change it in `.env` if the server already uses that range), and Keycloak takes `X-Forwarded-For` only from that range. Traefik drops whatever a client sends in those headers and in look-alike names (`X.Forwarded.For`). So login events and lock-outs carry the real address; anything else that reaches Keycloak is logged with its own address |
| Per-IP limits (Traefik) | Sign-in submissions (password, code, token): 5 a second, 10 at once in a burst, at most 10 in progress. "Forgot password" (sends an e-mail): 6 a minute. Sign-in pages: 30 a second (their CSS, scripts and fonts: no limit). Each hospital's sign-in start (`/api/ihr/auth/login/`): 5 a second; sign-out, the return from Keycloak and the session heartbeat are never limited, so a sign-out cannot be refused. Over the limit: HTTP 429 with `Retry-After`. Sized for about 200 staff behind one hospital address at a shift change; one account's password is protected by Keycloak's lock-out (5 failures) |
| Overload | Beyond 500 waiting requests Keycloak answers 503 at once instead of piling up |
| Events in the journal | Every sign-in, failure (WARN), lock-out (`USER_DISABLED_BY_TEMPORARY_LOCKOUT`) and admin change is a log line in the journal, kept 180 days, with the client address; they are also in Keycloak's database for 180 days. A password is never logged as such, but a failed sign-in records what was typed as the username: someone who types their password into the username box leaves it in the log (Keycloak's database keeps it too). P1-T9 removes the username of "user not found" events before shipping logs; alerts on bursts: P1-T9 |
| Admin console | Only through an SSH tunnel to `127.0.0.1:8081` (never on the public address); unchanged |

**Size of the event log (estimate):** 0.35–0.55 KB per event (measured); a staff member makes about a dozen
token refreshes an hour while working. At 100 hospitals with 100 people working that is roughly
1 GB a day before journald's compression; P1-T9 moves it to the log store.

**If a hospital sees "Too many requests"** (HTTP 429) at a shift change: its whole staff share
one address. Raise `login-ratelimit` in `traefik/routes.yml`, or give that hospital's address its
own router with a higher limit (Traefik's `ClientIP` rule).

## Client keys (P1-T8d2, ADR-012)

The backend, the onboarding worker and the staff worker prove who they are to Keycloak with
their own signing keys, not passwords: every hospital's sign-in client, the console and the two
worker accounts accept only a signed assertion. No Keycloak secret is in any env file any more
(the bootstrap admin of `master` stays, for operator one-offs and to enrol keys).

| Command | What it does |
|---|---|
| `scripts/staging.sh keys status` | Each key: id, state, created, age. `smoke` warns at 150 days and fails at 365 |
| `scripts/staging.sh keys rotate <backend\|onboarding\|staff\|all>` | **Every 180 days** (owner): a new key is made, published next to the old one, proven, switched to (no restart, no downtime), and after two minutes the old one is retired, proven refused and deleted. About 3 minutes per key; safe to run again if it stopped half-way |
| `scripts/staging.sh keys rotate <purpose> --compromised` | **Suspected theft:** always a brand-new key, switched at once (calls in flight may fail for a few seconds); for `backend` it first ends every staff and operator session, so everyone signs in again. If it stops half-way, run it again: an ordinary rotation of that key is refused until it has finished. The workers' tokens last one minute, so a stolen worker key's tokens die within a minute |
| `scripts/staging.sh keys revoke-sessions` | Ends every staff and operator session in both realms, on its own |
| `scripts/staging.sh keys probe [--negative]` | The backend's key is accepted; with `--negative`, every bad assertion (wrong key, expired, replayed, wrong audience, a secret, other algorithms) is refused |

**Where the keys are:** on the server only, each in its own Docker volume
(`clientkey-backend`, `clientkey-onboarding`, `clientkey-staff`), readable only inside its own
container; never on the deploying machine, never backed up. `up` creates them the first time
and publishes the public halves to Keycloak before the backend starts. A lost key (new server,
`down -v`) is simply replaced: `up` makes and enrols a new one. Rotations are logged to the
journal (key id and time only).

**First deploy after P1-T8d2 on an existing server:** `deploy.sh`, then `up` switches every
client to keys and makes the old worker secrets worthless; staff and operators stay signed in.
Then delete the old `deploy/staging/secrets/provisioner.env` and `staff-worker.env` on the
deploying machine (deploy.sh no longer ships them; `up` removes the server's copies).

## Database backups and restore drill (P1-T8c)

pgBackRest 2.60 keeps the whole database server (hospitals, platform, Keycloak) restorable to
**any moment of the last 28 days** (owner, 09-11-2026): a full backup every Sunday, a
differential the other nights (02:30 IST), and every change in between (WAL, shipped at least
once a minute). Retention is by time, so extra full backups never shorten the window.
Everything is **encrypted on the server** before it leaves (aes-256-cbc).

| Command | What it does |
|---|---|
| `scripts/staging.sh backup-init https://in-south1-objectstore.e2enetworks.net <bucket> Chennai --uri-style path` | Once: asks for the backup bucket's key (typed or piped, never an argument), makes the encryption passphrase, turns archiving on. AWS: `https://s3.ap-south-2.amazonaws.com <bucket> ap-south-2`. `--local`: a proof into this stack's SeaweedFS (automatic in `local-proof`; same disk, so not a real backup) |
| `scripts/staging.sh backup-status` | Backups in the repository, the service's last backup / push / check, WAL files still waiting. The service is **unhealthy** (Docker, and `smoke`) when a WAL file waits over 10 minutes, the hourly check fails, or no backup succeeded for 26 hours |
| `scripts/staging.sh backup-now [full\|diff]` | A backup now (e.g. before an upgrade) |
| `scripts/staging.sh restore-drill [--target "2026-11-09 14:00:55+05:30"]` | Restores into a throwaway copy beside the live database (refused without room for it), then checks every hospital's audit trail **in the copy** against the outside fingerprints the storage had by the copy's last transaction, and removes it. `--keep` leaves the copy for a look (`--clean` removes it). The target always carries its offset (+05:30): without one Postgres would read UTC |
| `scripts/staging.sh restore [--target …]` | **A lost database:** restores into this stack's own, empty database volume (never over an existing one, never while the stack runs), recovers with archiving on so the new timeline reaches the repository. Before recovery it sweeps the WAL spool: our database's surviving WAL is pushed and replayed (less is lost); another database's WAL (e.g. an empty database started by mistake) is set aside in the backup state volume's `rejected-wal/` (`backup-status` lists it), since it would block every later push |

**The passphrase:** `PGBACKREST_REPO1_CIPHER_PASS` in `secrets/backup.env`. Without it no backup
can be read, so copy it into the owner's password manager right after `backup-init` (the
deploying machine keeps `secrets/` too). Never only on the server.

**How it is built:** Postgres runs from our image (`deploy/docker/postgres.Dockerfile`: the
official 18.6 Alpine image + pgBackRest, as user 70 with no capabilities). It only copies each
finished WAL file into a spool volume; the separate `backup` service (same image, reads the data
read-only, `compose.backup.yaml`) pushes the spool every 10 s and runs the schedule. So the
database holds no backup key and still has no way out to the internet.

**A real restore** (the server or its disk is lost):
1. New server: `deploy/host/bootstrap.sh`, then `scripts/deploy.sh --no-start` with the same
   `.env` and `secrets/` (kept on the deploying machine). If they are gone too: `init`, then
   `backup-init <endpoint> <bucket> <region> --existing-passphrase` (the bucket key, then the
   passphrase from the password manager).
2. On the server, with the stack down: `scripts/staging.sh restore` (or `--target "…+05:30"` to
   undo a mistake made at a known moment). It first pushes any of the lost database's WAL still
   in the spool; if that fails (the repository out of reach) it stops before recovering anything:
   fix the access and run it again. An interrupted restore leaves a marker in the volume: `up`
   refuses it, and `restore` simply starts again.
3. `scripts/staging.sh up`, then `scripts/staging.sh backup-now full` (the first backup of the
   new timeline), then `scripts/staging.sh smoke`.
Practise with `restore-drill` monthly; it never touches the live database.

**An empty database never takes the place of a lost one.** `up` refuses to let Postgres make a
fresh, empty cluster (the hospitals would see an empty system) when:
- the server had a database before (`deploy/staging/.db-created`; only `down -v` clears it);
- a restore is unfinished (its marker in the volume);
- on a new server, the backup repository already holds backups (it is asked before the first
  start; if it cannot be read, `up` refuses too).
`up --new-database` starts an empty one on purpose. Should it happen anyway, the backup service
refuses to push the stranger's WAL (pgBackRest: "do not match the database") and turns unhealthy;
the repository stays untouched, and `restore` sets that WAL aside.

A drill fails the audit check on purpose ("no fingerprint has been written yet") until the copy
holds a transaction later than the server's first fingerprint (hourly, so in about its first
hour): a fingerprint stored after the copy's last transaction cannot vouch for it.

**Known and accepted:** the backup service reaches Postgres over its socket as the database
superuser (pgBackRest's local mode; it also reads every data file). It is exactly as trusted as
the database itself, has no published port, and is the only other container on that socket.

**Done in P1-T8b1:** the host (`deploy/host/bootstrap.sh`: firewall incl. Docker's published
ports, key-only SSH, NTP to NIC/NPL, journald one year, pinned Docker) and deploy over SSH.

**Done in P1-T8b2:** the wildcard certificate through our own acme-dns (above).

**Done in P1-T8b3:** files on SeaweedFS (above).

**Done in P1-T8b4:** mail (above) and the cloud modules (`infra/README.md`).

**Done in P1-T8c:** database backups and the restore drill (above); the fingerprint bucket's
lock mode (`IHR_AUDIT_ANCHOR_LOCK_MODE`).

**Done in P1-T8d1:** Keycloak in production (above).

**Done in P1-T8d2:** client keys (above).

**Not backed up:** SeaweedFS's files when they live on the server itself (owner, 09-11-2026: at
go-live files are in the provider's versioned bucket).

## Monitoring and alerts (P1-T9a)

Prometheus watches the stack and Alertmanager e-mails the owner (Q-E6) through Zoho ZeptoMail,
with an outside dead-man switch (healthchecks.io) and an outside uptime check (UptimeRobot)
for the cases the server cannot report itself (owner, 11-11-2026, Q-O1–Q-O4). Logs, their
redaction and the 180-day record are P1-T9b/c; dashboards P1-T9d.

**What runs** (`compose.monitoring.yaml`, on when `.env` has `IHR_MONITORING`; about 0.7 GB of
memory limits): Prometheus v3.16.0 (30 days, at most 4 GB), Alertmanager v0.35.0, node_exporter,
blackbox_exporter, postgres_exporter and redis_exporter, all pinned by digest, read-only, no
capabilities, journald logs. Prometheus reads its targets on the `monitoring` network (no way
out) and hands alerts over `alerting` (internal: Prometheus and Alertmanager only, plus the
dead-man stand-in in a proof). Alertmanager's API has no login, so it is on no network the web
app, Traefik or a scrape target shares: it listens on its fixed `alerting` address alone
(`IHR_ALERTMANAGER_IP`, default 10.203.0.20 in `IHR_ALERTING_NET` 10.203.0.16/28) and goes out
for mail and pings through `alertmail`, a bridge with no other container on it. The internet-facing
acme-dns is probed on `dnsprobe` (acme-dns and blackbox_exporter only), never on `monitoring`.
No container gets the Docker socket: a host timer (`deploy/host/bootstrap.sh` installs
`ihr-host-metrics.timer`) writes container states (long-running services only; one-shot set-up
services such as `migrate` or `traefik-data` exit by design and are never reported), memory
working sets (without reclaimable page cache, as `docker stats`), journal size, chrony's source
and offset, backup markers and client-key ages for node_exporter
(`deploy/monitoring/host-metrics.sh`). chronyd answers root only, so the unit's one root step
reads it into `/run/indiehr-chrony` (root-owned); a failed read shows as "unknown"
(NtpReadingFailed), never as a wrong source. `up` recreates Prometheus, Alertmanager or
blackbox_exporter when their settings changed since they started (a stamp of the files' hashes
in `generated/monitoring/`), so a new rule or a dead-man URL added later takes effect.

**What is read:** Traefik's metrics on its own port 8083, answered only to Prometheus's fixed
address (routes.yml, router `metrics`; never published); Keycloak's management port 9000 (never
routed by Traefik); SeaweedFS's gateway on 9327; Postgres through role `ihr_monitor` (pg_monitor
only, no per-table series, so no hospital schema names); Redis through ACL user `ihr-monitor`
(PING, INFO and the length of the `celery` list only: no other key, no CONFIG, which would show
the password; Redis holds only the SHA-256 of its password). Probes: `auth.<suffix>` sign-in
discovery and `status.<suffix>/healthz` through Traefik with the real certificate (expiry read
from it), `backend:9000/health/`, `keycloak:9000/health/ready`, and acme-dns answering on port 53.

**The public status address** `https://status.<suffix>/healthz` answers `OK` (200) from Traefik's
own ping, or 503 while Traefik stops. It carries no data and no version, and costs nothing per
request (no database query from the internet). It proves the server, its network, DNS, the
certificate and Traefik; what sits behind Traefik is watched from inside, and the dead-man switch
notices when that watching stops. `status` and `ops` are reserved slugs; every other path on
`status.` and all of `ops.` answer 418.

**Alerts** (rules in `deploy/monitoring/prometheus/rules/`, each unit-tested by promtool in
`../tests/`). Critical: mailed at once, repeated every 4 h. Warning: mailed between 08:00 and
22:00 IST, repeated every 24 h. A critical alert mutes the warning of the same name for the same
thing only (same service, job, instance, mount, key purpose and endpoint); a service down as a
whole (BackendHealth, KeycloakNotReady, BackupUnhealthy) mutes its container alerts. A container
alert can still arrive a few minutes first (it fires sooner); its repeats are then muted.

| Group | Alerts |
|---|---|
| Pipeline | Watchdog (always on, pings the dead-man switch about once a minute), DailyHeartbeat (a mail at 09:00 IST every day), AlertmanagerNotificationsFailing (also to the dead-man switch's `/fail`), AlertmanagerConfigNotLoaded, PrometheusRuleFailures, PrometheusCannotReachAlertmanager, HostMetricsStale |
| Availability | PublicEndpointDown (auth, status; 3 min), TargetDown (5 min; critical for traefik, keycloak, postgres, redis, s3, node), PostgresDown / RedisDown (the exporter answers but cannot reach its server or log in; 5 min), BackendHealth, KeycloakNotReady, ContainerNotRunning (critical for the hospitals' services, warning for the workers and monitoring), ContainerUnhealthy, ContainerRestarting (more than 2 in 15 min) |
| Capacity | DiskFreeLow (20 GB warning, 12 GB critical on `/`; other data mounts also below 25 % / 15 %; `/boot` never), DiskFillingUp (below 12 GB within 7 days), MemoryLow (10 % / 5 %), OomKill, SwapInUse, ContainerMemoryNearLimit (90 %), CpuLoadHigh, JournalNearCap, JournalSuppressed, PgConnectionsHigh (80 of 100), PgLongTransaction (1 h), RedisMemoryHigh (80 % / 90 %), RedisPersistenceFailing, CeleryBacklog (200 for 10 min) |
| Backups | BackupUnhealthy, BackupTooOld (26 h), BackupCheckStale (2 h), WalSpoolStuck (10 min), WalSpoolNearCap (75 %), WalArchiveFailing |
| Time, certificates, keys | NtpUnsynced, NtpOffset (chrony's own offset; 100 ms / 1 s), NtpSourceNotIndian, NtpReadingFailed, TlsCertExpiry (21 / 7 days), AcmeDnsDown, ClientKeyAge (150 / 365 days), ClientKeyMissing, ClientKeyCheckStale (also when never checked) |

Alert text is built from label values only (service, endpoint, job, key purpose): never a log
line, a user name, an address or patient data (`scripts/test_staging_monitoring.py` checks it).

| Command | What it does |
|---|---|
| `scripts/staging.sh monitoring-init owner@example.com [--from alerts@amhcloud.in]` | Once: asks for the alerts' own ZeptoMail token and the healthchecks.io ping URL (typed, or two lines on stdin; never the command line; the URL may be empty for now), writes `secrets/alertmanager-*`, `secrets/postgres-exporter.env`, `secrets/redis-exporter.env` (mode 600) and the settings in `.env`. The sender defaults to `alerts@` the domain above the suffix |
| `scripts/staging.sh monitoring-init --test` | Automatic in `local-proof`: mail to the Mailpit stand-in (STARTTLS, checked certificate, password), pings to a local counter; nothing leaves the machine |
| `scripts/staging.sh monitoring-deadman` | Add or change the ping URL later (stdin) |
| `scripts/staging.sh up` / `smoke` | `up` writes the settings, creates the database role, starts the services; `smoke` checks every target up, all rules loaded, Alertmanager configured with gossip off, `pg_up` and `redis_up` 1 (the exporters' logins work), the Watchdog reaching it (and the stand-in's ping count), Traefik's metrics refused to other containers, the host file fresh, the status address |
| `scripts/staging.sh alerts` | What Alertmanager holds now (amtool) |
| `scripts/staging.sh alert-test` | One test alert (`IhrAlertTest`, severity info), mailed within about 30 s, resolved after 5 min; with the stand-in it waits for the mail |
| `scripts/staging.sh ops-tunnel ihr@<server>` | SSH tunnel: Prometheus at http://127.0.0.1:9090, Alertmanager at http://127.0.0.1:9093 (their fixed addresses on the server's `monitoring` and `alerting` networks; nothing published) |
| `scripts/check-monitoring.sh` | promtool and amtool from the pinned images: settings, rules, rule tests (pre-commit and CI) |

**Owner, before monitoring goes on:**
1. ZeptoMail: a second Mail Agent for alerts (its own token, so it can be revoked alone), sending
   from `alerts@amhcloud.in` (same verified domain). Copy its SMTP password.
2. healthchecks.io (free plan): a check with period 1 minute and grace 10 minutes; e-mail
   integration to the owner. Copy its ping URL (`https://hc-ping.com/<uuid>`). It receives only
   the Watchdog alert's name and Alertmanager's own address, never data.
3. UptimeRobot (free plan): an HTTP(S) monitor on `https://status.app.amhcloud.in/healthz`,
   keyword `OK`, every 5 minutes, alert by e-mail.
4. `scripts/staging.sh monitoring-init <your address>` (token, then URL), deploy, `up`, `smoke`,
   then `scripts/staging.sh alert-test` and look for the mail.

**Privacy fix that came with this task:** Postgres no longer writes statements into its log
(`log_min_duration_statement=-1`, `log_min_error_statement=panic`, `log_error_verbosity=terse`,
also for restored copies and the dev database). Django binds values on the client side, so a
logged slow or failed statement carried what staff typed (names, phone numbers), and a
duplicate-key error's DETAIL line carried the key's value.

**Not proven yet** (needs the stack running; this task checked the settings statically and with
the pinned images): targets up, mails arriving, the dead-man pings, memory use, the host timer on
a server.
