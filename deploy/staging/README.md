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
| Events in the logs | Every sign-in, failure (WARN), lock-out (`USER_DISABLED_BY_TEMPORARY_LOCKOUT`) and admin change is a log line with the client address: in the journal for 14 days (a buffer), redacted in the log store for 180 days and in the archive for 400 (P1-T9b, P1-T9c); they are also in Keycloak's database for 180 days. A password is never logged as such, but a failed sign-in records what was typed as the username: the log store and archive drop it when no account matched (P1-T9b); the 14-day journal and Keycloak's database keep it. Alerts on bursts: "Logs" below |
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

## Logs: collection, redaction and log alerts (P1-T9b, ADR-013)

Every line the server writes to its journal goes to a log store you can search, with personal
data and secrets removed first, and sign-in abuse raises an alert mail (owner, 12-11-2026,
Q-O5 to Q-O8). The 180-day archive and the shorter journal follow in P1-T9c, and dashboards in
P1-T9d.

**What runs** (`compose.logs.yaml`, together with the monitoring; about 35 MB in use, limits
448 MB):

- **Fluent Bit 5.2.0** reads the whole journal from a read-only mount, as `nobody` with the
  host's `systemd-journal` group. That covers every container (each tagged with its compose
  service) and the host's own SSH, sudo, firewall and systemd lines.
  - Its read position and unsent lines are kept in its volume `fbstate`.
  - If the log store is away, lines wait on disk (up to 1 GB) and are sent once it is back.
- **VictoriaLogs v1.53.0** keeps the redacted lines for 180 days, never using more than
  `IHR_LOGS_MAX_SIZE` of disk (default 20GiB; past that, the oldest days go).
- **vmalert v1.156.0** runs the log alerts every minute and hands them to the existing
  Alertmanager.

All three are pinned by digest, read-only, with no capabilities, and not running as root.

**Isolation:**

- The three share the internal `logs` network (`IHR_LOGS_NET`, default 10.203.0.32/28) with
  Prometheus and nobody else. The web app, Traefik and the exporters cannot reach the store.
- The store asks for a password even for reading: user `ihr-logs`, password in
  `secrets/logs-http-password`. Fluent Bit and vmalert hold it, and you type it in the browser.
- It sends no cross-origin headers, so a web page open in your browser while the SSH tunnel runs
  cannot read it. DNS rebinding meets the password.
- Its `/metrics` has a key of its own (`secrets/logs-metrics-key`). Prometheus's target file
  carries it; it shows counters only.
- Anything that deletes or rewrites logs (delete, drop a partition, force merge or flush) or shows
  its settings needs the key in `secrets/logs-admin-key`. Only the operator holds that key;
  nothing on the network has it.

**Redaction** (`deploy/logs/fluent-bit/redact.lua`), before anything leaves Fluent Bit:

- **Journald's own fields** (command lines, IDs) are dropped.
- **Only Docker's own lines count as a container's.** journald's trusted fields must say Docker
  wrote the line. A host process that tags its lines `keycloak` or `traefik`, or adds Docker's
  field names, stays a host line and can never pass for one of them.
- **Keycloak sign-in events:**
  - Each event is parsed strictly. Every value must be quoted, a backslash escapes only a quote,
    no key may repeat, and Keycloak's fixed head (type, realm, client, user, address, error) must
    come first. Event fields (`kc_event`, `kc_error`, `kc_user`, `client_ip`) come from that head
    only.
  - Text typed into the username box therefore cannot pose as an event field or another address.
    A line that fails these checks loses everything from its username to the end, and gets no
    event fields.
  - Each event is rebuilt from a list of known fields; unknown fields are dropped and named in
    `kc_dropped`.
  - The username someone typed is dropped when no account matched (it may be a password typed
    into the wrong box). A real account's username is kept, for lock-out checks (owner's choice).
  - E-mail details are replaced by a marker. Return addresses lose their query.
- **Every other text** has these masked:
  - tokens: JWTs, Bearer and Basic values, `password=`/`token=`/`secret=` style values;
  - anything after `?` in an address, including a relative one (`users?email=...`);
  - values echoed in database errors, from the marker to the end of the line (values may hold
    unbalanced brackets or quotes);
  - e-mail addresses, also URL-encoded;
  - Indian mobile numbers in their usual shapes;
  - 12-digit (Aadhaar-like) numbers;
  - ABHA numbers and addresses;
  - usernames in `/api/v1/users/<name>/`.
- **Kept on purpose:** IP addresses (CERT-In), UUIDs and times.
- **On failure:** a script error drops that one line, never ships it unmasked. Prometheus alerts
  on such drops.
- **Tests:** `scripts/check-logs.py` runs the redaction cases (`deploy/logs/tests/`) through the
  pinned images (pre-commit and CI). A new kind of line that leaks is fixed by adding a case.

**Log alerts** (`deploy/logs/alerts/sign-in.yml`). The mail names the address (`client_ip`,
checked to be an IP address before it is stored):

| Alert | When | Severity |
|---|---|---|
| SignInLockoutsFromAddress | 3 or more distinct staff accounts locked out from one address in 15 min | warning, mailed day or night |
| SignInLockoutStorm | 10 or more distinct accounts locked out in 15 min, all addresses | critical |
| FailedSignInsFromAddress | 30 or more failed sign-ins from one address in 15 min: wrong passwords, unknown users, tries on a locked or disabled account (session errors such as an expired sign-in page do not count) | warning, mailed day or night |
| RateLimitedAddress | 100 or more "too many requests" (429) answers to one address in 10 min, for 5 min | warning, mailed day or night |

The three per-address alerts carry `notify: anytime`. Alertmanager mails them at once, day or
night, at most daily per address (owner, Q-O9). Other warnings still wait for 08:00–22:00 IST.
They arrive about 2 minutes after the event: vmalert waits 30 s for late lines, then runs once a
minute. Only container lines count. A hospital whose staff share one address and outgrow the per-address limit shows up as
RateLimitedAddress: raise its limit (see "Per-IP limits" above).

**The pipeline itself** (Prometheus, `rules/logs.yml`):

- LogRedactionDrops
- LogShippingFailing (15 min)
- LogLinesLost
- LogIngestStalled (nothing for 30 min)
- LogLinesRejected
- LogStoreReadOnly (critical)
- LogStoreNearCap (85 % of `IHR_LOGS_MAX_SIZE` for 1 h)
- LogAlertsFailing

TargetDown and ContainerNotRunning cover the three services (warnings: the journal still holds
every line).

| Command | What it does |
|---|---|
| `scripts/staging.sh monitoring-init …` | Also writes the log store's secrets: `logs-admin-key`, `logs-http-password` (+ `logs-shipper.env` for Fluent Bit) and `logs-metrics-key`, all mode 600. `up` makes any that are missing on a server set up before P1-T9b |
| `scripts/staging.sh up` / `smoke` | `up` starts the three services and recreates Fluent Bit or vmalert when their settings changed. `smoke` writes a test line with a synthetic e-mail, mobile, Aadhaar-like number, token and query into the journal and checks it arrives masked, with none of the values. It also checks the host's own lines arrive, vmalert's rules run, reading without the password and deleting without the key are both refused (401), and the web app cannot reach the store. It reads the store through a one-off container on its network, with the password on stdin |
| `scripts/staging.sh ops-tunnel ihr@<server>` | Also: the log store's search page at http://127.0.0.1:9428/select/vmui/ (user `ihr-logs`, password from `secrets/logs-http-password`) and vmalert at http://127.0.0.1:8880 |
| `scripts/check-logs.py` | Redaction cases, the store and the log alerts with the pinned images (pre-commit and CI; Docker, about 30 s) |

**Searching** (vmui, LogsQL):

- `service:=keycloak kc_event:=LOGIN_ERROR`
- `service:=traefik http_status:=429 | stats by (client_ip) count()`
- `source:=host service:=sshd`

Stored fields are `host`, `source` (container or host), `service`, `container`, `image`,
`unit`, `pid`, `priority`, plus `kc_event`, `kc_error`, `kc_user` (the account id, real accounts
only) and `client_ip` on Keycloak events, and
`client_ip`, `http_status` and `router` on Traefik's access lines.

**A proof on this machine** writes the stack's logs to this machine's journal only when `.env`
says `IHR_LOG_DRIVER=journald`. That adds `compose.local-journal.yaml`, and Fluent Bit then
starts at the journal's end (`IHR_LOGS_FROM_TAIL=on`). On a server, the first start reads the
whole existing journal once.

**Also in this task:** CARE's deployment settings switched off gunicorn's, Django's and Celery's
loggers, so a server error on staging left no trace at all. They are on again
(`disable_existing_loggers: False` in our deployment settings), and their lines are redacted like
all others.

## Log archive and the 180-day record (P1-T9c, ADR-014)

**What makes up the record** (owner, 11/12-11-2026: Q-O3, Q-O10 to Q-O12):

| Part | Where | How long | What |
|---|---|---|---|
| Journal | this server | **14 days** (a buffer) | raw lines, until Fluent Bit has shipped them (`deploy/host/bootstrap.sh`, `deploy/host/README.md`) |
| Log store | this server (VictoriaLogs) | **180 days** (CERT-In) | redacted lines, searchable (section "Logs" above) |
| Archive | backup region (E2E Chennai at go-live) | **400 days** (DPDP Rules 8(3): a year, plus margin), then erased | redacted lines, one encrypted, locked file per India day |

**The archiver** (`compose.logs-archive.yaml`, on when `.env` has `IHR_LOGS_ARCHIVE`;
`deploy/logs/archive/archive.py`, run from the backend image):

- **When:** each India day (00:00–24:00 IST) is archived once it has been over for an hour.
- **Export:** hour by hour from the store, each hour's count checked before and after (the store
  ends a slow query early without an error, so an hour that does not match is tried again, then
  refused).
- **Format:** gzip, then encrypted with our key (`IHR_LOGS_ARCHIVE_ENCRYPTION_KEY`, Fernet).
- **Upload and check:** written once (If-None-Match), with Content-MD5, into a bucket with
  Object Lock (400 days), then read back and decrypted to check its lines.
- **Manifests:** a small signed manifest goes beside each day, holding lines per hour, size,
  SHA-256 and the previous manifest's SHA-256. The chain is followed by its links: one start, no
  forks, no gaps. A missing, replaced or forged day shows.
- **Late lines:** every day still in the store is recounted on every pass (every 10 minutes). A
  day that gained lines, because Fluent Bit delivered a backlog late, is archived again as the
  next revision, and the earlier one stays.
- **Revisions only grow:** a later revision is a re-export of the same day, so it must hold every
  line of the one before. `check` compares the hourly counts; `check --deep` compares the lines
  themselves. Someone holding the server's keys therefore cannot slip in a cleaned copy of a day.
- **An interrupted upload** (a file stored, its manifest not) leaves a locked name behind. The
  next pass simply uses the next revision; nothing waits for you.
- **Full check:** once a week the archiver decrypts every file and checks every revision against
  the one before. Its result feeds LogArchiveNotAuthentic.
- **Footprint:** about 46 MB of memory in use (256 MB limit).
- **Networks:** `logs` and an egress bridge of its own. In a proof it also joins `internal`,
  where the stand-in is.

**Keys:**

- **Writer key (server):** it can add and read under `logs/`, never delete, hide or unlock
  anything. On the stand-in its policy is proven by `s3-check`. On E2E the bucket's Writer role
  is the closest match; check its rights at go-live (below).
- **Encryption key (server and your password manager):** without it the archive cannot be read.
- **Admin key (your machine only, typed when needed):** needed only for erasure.

| Command | What it does |
|---|---|
| `scripts/staging.sh logs-archive-init https://in-south1-objectstore.e2enetworks.net <bucket> Chennai [--uri-style path\|host]` | Once: asks for the bucket's Writer key (typed or two lines on stdin), makes the encryption key, writes `secrets/logs-archive.env` (mode 600) and `IHR_LOGS_ARCHIVE=s3`. **Copy `IHR_LOGS_ARCHIVE_ENCRYPTION_KEY` into your password manager.** `--existing-key`: a rebuilt server continuing an archive (the key typed, or the last line on stdin). `--local`: a proof into this stack's SeaweedFS (automatic in `local-proof`) |
| `scripts/staging.sh logs-archive-check [--deep] [--recent N]` | Every manifest and erasure record authentic, the chain whole, every file present with its size, and the bucket's lock. `--recent N`: only the last N days' files fetched and their SHA-256 checked (`smoke` uses 2; the default fetches all). `--deep`: every file also decrypted, its lines counted, and each revision checked to hold every line of the one before |
| `scripts/staging.sh logs-archive-get <YYYY-MM-DD> [revision] > day.jsonl` | One day back as JSON lines (the latest revision unless one is named), checked and decrypted; a day's revisions are listed on the terminal. For an incident report to CERT-In older than the store's 180 days |
| `scripts/staging.sh logs-archive-probe` | What the server's archive key may do (add, read; never delete, hide, unlock or write outside `logs/`) and whether the bucket is locked for 400 days. Run it at go-live: E2E documents neither. It leaves a small probe file under `logs/ihr-key-check/`, erased with the rest after 400 days |
| `scripts/staging.sh logs-archive-now` | A pass now (the service does one every 10 minutes) |
| `scripts/staging.sh logs-archive-expire [--dry-run] [--older-than-days 400]` | **Monthly, from your machine** (the deploy folder with `.env` and `secrets/`): asks for the bucket's admin key and deletes every version of each day older than 400 days, lifting the Governance lock. Run `--dry-run` first. Safety: it refuses fewer than 400 days, refuses when your machine's clock is more than an hour off the bucket's, and never touches a file whose own lock still runs. **Before the first deletion** it writes a signed erasure record (`erasure/<time>.json`: the days, the files, each erased manifest's hash and its predecessor), so `logs-archive-check` follows the chain through the erased days; a second record says how it ended. The record's key is derived from the archive key on your machine; the erasure tool never sees the archive key itself |
| `scripts/staging.sh logs-volume [hours]` | Lines and MB a day in the store over the last hours (default 24), and what 180 days, the archive and 14 days of journal need (the journal: its size over the days it holds) |

**Alerts** (`rules/logs.yml`, "log-archive"):

- **LogArchiveBehind:** more than a day behind (warning), more than four days (critical).
- **LogArchiveFailing:** passes fail for 2 h.
- **LogArchiveNotAuthentic** (critical): a manifest or erasure record we did not write or that
  was changed, a broken chain, or (the weekly full check) a revision that drops lines.
- **LogArchiveNotLocked** (critical): the bucket is no longer locked for 400 days with
  versioning.
- **LogArchiveShrunk:** a recent day has fewer lines in the store than in its archive.
- **LogArchiveErasureOverdue:** a day older than 435 days is still there. Run the monthly
  erasure.
- **JournalBufferShort** (`rules/capacity.yml`): the journal no longer reaches back 14 days on a
  host older than 15 days. Raise `SystemMaxUse`.

**Sizes (measured on the proof stack, 12-11-2026):**

| What | Cost |
|---|---|
| One web request | 3 log lines (Traefik, nginx, the backend), about 1.8 KB sent to the store, about 130 bytes on its disk (compression about 14x) |
| Same request in the journal | about 2 KB |
| An idle server | some thousands of lines an hour (probes, timers, kernel firewall lines) |

| Per 100,000 requests a day | Size |
|---|---|
| Store, 180 days | about 2.3 GB |
| Archive, 400 days | about 6 GB |
| Journal, 14 days | about 2.8 GB |

So:

- the defaults (store 20 GiB, journal 8 GB) hold about 850,000 and 250,000 requests a day;
- on a server, `logs-volume` measures the real figures; raise `IHR_LOGS_MAX_SIZE` or the
  journal cap when it says so (LogStoreNearCap and JournalBufferShort warn first);
- E2E's object storage charges ₹625 a month for the first 250 GB (+ GST).

**At go-live (owner):**

1. In E2E's console, in **Chennai**, create a bucket **with Object Lock**: Governance, default
   retention 400 days. Lock can only be chosen at creation; it also turns versioning on for
   good.
2. Create an access key with the **Writer** role on that bucket only. Separately, keep the
   bucket's **admin** key in your password manager; it is used only for erasure.
3. On your machine: `scripts/staging.sh logs-archive-init https://in-south1-objectstore.e2enetworks.net <bucket> Chennai --uri-style path`
   (if E2E refuses path-style, use `--uri-style host`). Put the Writer key in when asked, then
   copy the encryption key into your password manager. Deploy.
4. On the server: `scripts/staging.sh logs-archive-probe`. Every line must say PASS. E2E does
   not document whether its Writer role can delete. If a delete line fails, ask E2E for a role
   without delete, or plan the move to AWS (COMPLIANCE, put-only key). Then run
   `logs-archive-now` and `logs-archive-check`, and on your machine
   `logs-archive-expire --dry-run` (it lists, deletes nothing).
5. If E2E does not support `If-None-Match` on upload, set `IHR_LOGS_ARCHIVE_IF_NONE_MATCH=off`
   in `secrets/logs-archive.env`. The lock still keeps every version.

**Known and accepted:**

- **E2E's Governance lock** stops the server's key, but not E2E's account admin (Q-E7, Q-O10).
  The chain still shows a removed or changed day to `logs-archive-check`.
- **Losing the server** loses at most the current day, plus the hour before it is archived.
  `logs-archive-check` and `logs-archive-get` also run from your machine without the server (a
  one-off container with `secrets/logs-archive.env`), for an incident report.
- **The archiver's health check only asks whether it answers.** A failing pass shows as
  LogArchiveFailing, not as a sick container.
- **In a proof,** erasing a day the store still holds makes the archiver archive it again. On a
  server, erased days (400 days) are long gone from the store (180).

## Dashboards (P1-T9d, ADR-015)

Grafana shows the server, the data and backups, traffic and sign-ins, and the redacted logs, on
four read-only dashboards in the folder "IndieHR". It is a viewer: alerts still come only from
Prometheus, vmalert and Alertmanager. It starts with monitoring (`compose.dashboards.yaml`).

**Open it:**

1. Run `scripts/staging.sh ops-tunnel ihr@<server>` on your own machine.
2. Open http://grafana.localhost:3000 (any browser on Linux, macOS or Windows sends
   `*.localhost` to this machine).
3. Sign in. The name is `GF_SECURITY_ADMIN_USER` in `secrets/grafana-admin.env` on the server,
   and the password is in `secrets/grafana-admin-password`. Both are generated at the first
   `up` and belong in the owner's password manager.

**What it can reach:**

- It is on the internal `dashboards` network (10.203.0.48/28), with Prometheus and
  `logs-reader` only. It listens on its fixed address (10.203.0.50) and on no other.
- Nothing that takes traffic from the internet can reach it.
- It reads the logs through `logs-reader` (vmauth) with its own read-only login (`grafana-ro`).
  It never holds the log store's password, and it can neither insert nor delete lines.
  `logs-reader` has its own key for its own pages (`secrets/logs-reader-key`); the store's
  admin key stays with VictoriaLogs.

**Changes:**

- **Dashboards.** Edit `deploy/grafana/build_dashboards.py`, run it, and commit the script and
  the JSON. UI edits are not saved. A running Grafana picks up new JSON within a minute.
- **Datasources and the log door.** Changes to `deploy/grafana/provisioning/` or
  `logs-reader.yml` recreate the container on the next `up`.

**Locked out or lost password:**

- Five wrong passwords lock the name for 5 minutes, and then it unlocks by itself.
- `scripts/staging.sh grafana-reset-admin` sets a new password in Grafana and in the file
  together.
- Do not edit or delete the secret files by hand. Grafana stores the account at its first start,
  and `up` refuses to make new files while Grafana's data volume exists.

**Checks:** `smoke` runs `scripts/check-dashboards.py`. It covers:

- **Sign-in:** health; another host name redirected; the admin login works, a wrong password or
  the name `admin` is refused; basic auth off.
- **Browser and settings:** the CSP; `/metrics` closed; a write from another origin refused;
  every setting that could call out is off.
- **Content:** exactly two datasource plugins; both datasources healthy; the dashboards as in
  git; every metric a panel names exists; every panel's query runs and returns data. A panel may
  be empty only if `deploy/grafana/allowed-empty.txt` lists it, with a reason, for a case that
  applies here. `smoke` works the cases out: a proof, no archive or backups, an archive with no
  whole day yet.
- **Reach** (checked by `smoke` itself):
  - the web app cannot reach Grafana;
  - Grafana reaches Prometheus but not the log store or Alertmanager;
  - its log login reads but cannot write, and without a login nothing answers;
  - the store's admin key is not in the log door.

**Log levels:** host lines are coloured by journald's priority, container lines by the level in
their message. Docker marks all of stderr as an error, and most services log to stderr.

**Memory budget** (limits against measured use on the local proof, 2026-11-12, after the dashboards
were opened): Grafana 512 MB against 227 MB (270 MB idle on its own), logs-reader 48 MB against
6 MB. The limits of all services add up to more than the server's 8 GB. They are ceilings that
stop one runaway service, not reservations: the whole stack used 2.2 GB on the proof (Keycloak
614 MB the largest).
