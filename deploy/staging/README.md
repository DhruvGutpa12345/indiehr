# Staging stack (P1-T8a)

One VM in an Indian region (provider-neutral, ADR-011), Docker Compose, Traefik in front
(ADR-003). Everything runs through `scripts/staging.sh`; the server itself is set up by
`deploy/host/bootstrap.sh` and the stack reaches it through `scripts/deploy.sh` (P1-T8b1).

| Command | What it does |
|---|---|
| `scripts/staging.sh init <suffix>` | Writes `.env` (site settings, image digests) and `secrets/*.env` (mode 600, git-ignored). Refuses to overwrite. `<suffix>`: hospitals live at `<slug>.<suffix>` |
| `scripts/staging.sh up` | Database, Redis, the platform's (public) migrations, web app, Celery, Keycloak, Traefik; then the Keycloak realms and the two workers' narrow accounts. Never migrates a hospital (P1-T18a): if a live hospital's schema is not the new build's, the `migrate` one-shot refuses and nothing starts; migrate hospitals in the announced window (`staging.sh release`, P1-T18b: see "Releases" below). A hospital this build's release already tried and left held (its migration failed) stays at 503 and the others start |
| `scripts/staging.sh release` / `rollback` / `releases` | The new images in the announced window, rolling back, the releases in force (P1-T18b): see "Releases" below |
| `scripts/staging.sh smoke` | 20 checks through Traefik: what is public and what is not, TLS redirect, HSTS, token issuer, production mode, no query strings in logs; and every active hospital served (a held one, 503 "upgrading", fails it: P1-T18b) |
| `scripts/staging.sh manage --operator <you> --reason "<ticket>" --hospital <slug>\|all <args>` | One-off `manage.py` as the database owner `ihr_owner` (its output never logged; its session start and end are: "Operator access record (P1-T27)"; no Keycloak admin credential) |
| `scripts/staging.sh db-split-secrets` / `db-roles --migrate \| --serve-previous \| --assert \| --status` | The split database roles (P1-T19b): their secrets, moving a restored database, serving the build that ran after a release stopped before the move, re-asserting them: see "Moving to split roles" below |
| `scripts/staging.sh start-existing <service>…` | Starts the existing (stopped) containers of app services again as they were: their own image, settings and secrets, with `docker start`. Never `up` (it would recreate them with this stack's new images and secrets) and never `docker compose start`: Compose also starts each service's dependencies, and every app service depends on the one-shot `migrate`, which has no container on this stack (it runs with `compose run --rm`), so it fails with `missing dependency migrate` and starts nothing. Refuses a service that is not an app service or has no container left, and starts no one-off left by `compose run` |
| `scripts/staging.sh local-proof` | All of the above on this machine (127.0.0.1, self-signed certificate) |
| `scripts/staging.sh acme-init …` etc. | The wildcard certificate: see below |
| `scripts/deploy.sh --host ihr@<address> --operator <you>` | From this machine to a bootstrapped server: builds our two images (web app from the CI bundle), sends them with `docker save \| ssh docker load` (skipped when the server has that build), sends the tracked stack files and, as a tar stream over SSH, `.env` + `secrets/` (mode 600), then runs `staging.sh up` and `smoke` there (or, with `--release`, `staging.sh release`: required when the build changes a schema, decided before anything is sent; see "Releases"), logs the release once smoke passes, removes release images beyond the last 3 logged releases and untagged ones, and writes `/srv/indiehr/DEPLOYED` (commit and dirty flag as labelled on the images, image IDs, web bundle id). `/srv/indiehr/DEPLOYING` is written first and removed only on success: if it is still there, the last deploy failed part-way. The backend image is built from the committed files (`git archive`), never from git-ignored ones. Refuses a dirty git tree without `--allow-dirty`, images built from another commit with `--no-build` unless `--allow-dirty`, and a server whose host key is not yet in `known_hosts` |
| `scripts/deploy.sh --local-proof --operator <you>` | The same into the throwaway VM of `scripts/local-vm.sh` (settings from `staging.sh init app.ihr-proof.in --vm`, kept in `~/.cache/indiehr/vm/staging`); runs `staging.sh local-proof` there (hospital `alpha` + smoke). Runbook: `deploy/host/README.md` |

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
the app role's database address (`ihr_app`), Redis); the web app also the audit bucket's
read-only key, the Celery worker its add-only key; each Keycloak worker only its own narrow
account; Keycloak its database password and first-start admin, which `staging.sh` passes only to
one-off commands. Only `migrate`, `admin` and the provisioner also get `db-owner.env`, the
database owner's address (`ihr_owner`; P1-T19b).

**Settings** (`ihr_platform.settings.deployment`): the server refuses to start, naming every
problem, without its own secret key, signing keys, hosts, https addresses, secure cookies, HSTS,
production mode, or with CARE's terminology default or Sentry (`settings/secure.py`). It also
refuses any `TIME_ZONE` other than `Asia/Kolkata`, or `USE_TZ` off (P1-T7b), and
`IHR_DB_ROLES` other than `required` or a container that does not declare its database role
(`IHR_DB_ROLE`: app or owner; P1-T19b).

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
  address), `BUCKET_REGION`, the two bucket names, and **two keys** (P1-T20b): `FILE_UPLOAD_KEY` /
  `FILE_UPLOAD_SECRET` reaching only the patient files bucket, `FACILITY_S3_KEY` /
  `FACILITY_S3_SECRET` reaching only the pictures bucket; never `BUCKET_KEY` (the backend refuses
  to start with it, or with one key for both buckets). Both buckets private, with the same CORS
  (`https://*.<suffix>`), no public-read policy (`deploy/s3/setup_buckets.py` sets this against
  any S3 endpoint and removes an old public-read policy);
- `secrets/anchor-read.env` / `anchor-write.env`: `IHR_AUDIT_ANCHOR_ENDPOINT` (empty for AWS),
  `_BUCKET`, the two keys;
- copy the files across first; keep the old `s3data` volume until its fingerprints are copied
  or no longer needed (their lock cannot be lifted).

| Command | What it does |
|---|---|
| `scripts/staging.sh s3-init` | Once, after `init`: makes the keys (storage admin, CARE's patient files, CARE's pictures, fingerprint writer, fingerprint reader, backups, log archive) in `secrets/s3-gateway.env`, gives CARE (`secrets/app.env`: `FILE_UPLOAD_*`, `FACILITY_S3_*`) and the two fingerprint files their keys, and adds the settings to `.env` |
| `scripts/staging.sh s3-split-keys` | **On the deploying machine** (`IHR_STAGING_OUT=<dir>` for another folder, as `db-split-secrets`), once, for a site set up before P1-T20b: two new keys (patient files, pictures) into `secrets/s3-gateway.env` and `secrets/app.env` (`FILE_UPLOAD_*`, `FACILITY_S3_*`), the combined `IHR_S3_CARE_*` and `BUCKET_KEY`/`BUCKET_SECRET` taken out, the old pair kept in `secrets/s3-retired.env` only so `s3-check` can prove it dead. Prints file names only; idempotent (after an interruption it finishes from the gateway's keys). `--check` only says whether the keys are split |
| `scripts/deploy.sh` | Refuses, before anything is sent, a folder whose keys are not split (it names `s3-split-keys`): each deploy sends that copy over the server's, so the keys are made where they are kept, never on the server. The throwaway VM's folder (`--local-proof`) is split for you |
| `scripts/staging.sh up` | Also starts SeaweedFS and sets up the buckets (idempotent). On a server, `up`, `release`, `db-roles --migrate`, `rollback` and every start of the s3 container (backup reads, restores) refuse unsplit keys **before anything changes** (no hold, no migrate one-shot, no key made there); a local proof (`.local`) splits its own |
| `scripts/staging.sh s3-check` | Live checks: each key does what it must and nothing more (the patient files key cannot touch a picture, the pictures key cannot touch a patient file), the retired combined key is unknown to the gateway, the lock holds against every key including the admin, anonymous and browser access through `files.<suffix>` (a picture needs a signed link) |

**Buckets:** `FILE_UPLOAD_BUCKET` (private: the browser uploads and downloads with short-lived
signed links), `FACILITY_S3_BUCKET` (staff photos and facility covers, private too since P1-T20b,
owner answer Q-T3: the web app gets a 24-hour signed link for each picture of its own hospital,
nobody reads, lists or writes anonymously), and
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
| `scripts/staging.sh restore-drill [--target "2026-11-09 14:00:55+05:30"]` | Restores into a throwaway copy beside the live database (refused without room for it), gives the copy's roles the secrets' passwords (and moves them there too if the copy predates the move of the roles, P1-T19b), then checks every hospital's audit trail **in the copy**, as `ihr_owner`, against the outside fingerprints the storage had by the copy's last transaction, and removes it. `--keep` leaves the copy for a look (`--clean` removes it): a data-tier session, and the way into it is `scripts/staging.sh restore-drill-psql --operator <you> --reason "<ticket>" [psql args]` (psql as postgres in the copy, recorded like `manage`; never a raw `docker exec`, which PrivilegedLoginUnrecorded flags). The target always carries its offset (+05:30): without one Postgres would read UTC |
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
2. On the server, with the stack down: `scripts/staging.sh restore` (to the newest moment), or
   `restore --target "…+05:30" --operator <you>` to undo a mistake or lose a server at a known
   moment. **`--target` is a disaster restore with audit events: read "Disaster restores" below
   first** (it needs the owner's events key and the fingerprint writer's key, and writes a stub
   plan before it replaces anything; without a fingerprint writer key it is refused unless
   `--no-supersede --reason "<the incident>"`). It first pushes any of the lost database's WAL still
   in the spool; if that fails (the repository out of reach) it stops before recovering anything:
   fix the access and run it again. An interrupted restore leaves a marker in the volume: `up`
   refuses it, and `restore` starts it again from scratch.
   **A retry of `restore --target` (another T, or a failed attempt)** leaves the first attempt's
   stub in the bucket (write-once; it alerts as pending until the next attempt's result). A
   failed attempt is marked `abandoned` by itself; a restore that did finish but is to be redone:
   `scripts/staging.sh audit-supersede abandon <id> --reason "<why>"`, then `down`, remove the
   volume `docker volume rm <project>_pgdata` (never `down -v`: it also deletes the log store, the
   signing keys and the WAL spool) and run `restore --target` again.
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
ports, key-only SSH, NTP to NIC/NPL, journald as a 15-day buffer (ADR-014; the log store keeps 180 days), pinned Docker) and deploy over SSH.

**Done in P1-T8b2:** the wildcard certificate through our own acme-dns (above).

**Done in P1-T8b3:** files on SeaweedFS (above).

**Done in P1-T8b4:** mail (above) and the cloud modules (`infra/README.md`).

**Done in P1-T8c:** database backups and the restore drill (above); the fingerprint bucket's
lock mode (`IHR_AUDIT_ANCHOR_LOCK_MODE`).

**Done in P1-T8d1:** Keycloak in production (above).

**Done in P1-T8d2:** client keys (above).

**Not backed up:** SeaweedFS's files when they live on the server itself (owner, 09-11-2026: at
go-live files are in the provider's versioned bucket).

## Hospital dumps (P1-T22a)

pgBackRest (above) can only bring back **the whole server**. To undo one hospital's mistake
without rolling back every other hospital, each hospital is also dumped on its own every night
(design: `docs/reviews/p1-t22/design.md`, "After the attack"; sizes:
`docs/reviews/p1-t22/size-model.md`; proof: `docs/reviews/p1-t22/proof-a.md`). A hospital is
restored from them with `hospital-restore` (next section).

**The dumper** (`compose.dumps.yaml`, on when `.env` has `IHR_DUMPS`; `deploy/dumps/dumper.py`
with `deploy/lib/`, in our dumper image `deploy/docker/dumper.Dockerfile`: the database's own
Postgres 18.6 image plus Python, so `pg_dump` matches the server):

- **When:** 03:30 IST (`IHR_DUMP_TIME`), after the 02:00 release window and the 02:30 backup;
  nothing starts inside the quiet window 01:45–03:25 (`IHR_DUMP_QUIET`). A fresh or restarted
  dumper first dumps every hospital without a good dump in the last 26 hours.
- **Who:** active, suspended and offboarding hospitals, 2 at a time (`IHR_DUMP_PARALLEL`, 1 to 4). A
  hospital **held** for a release is skipped and reported (`ihr-dump: hospital=<slug>
  outcome=held`); provisioning and archived ones are not dumped.
- **How, per hospital:** its advisory lock (the one provisioning and the fleet runner take;
  waited for 10 minutes), on the same session one REPEATABLE READ snapshot in which the
  manifest's facts and the hospital's own rows in `public` are read, then `pg_dump -Fc
  --compress=zstd -n <schema> --snapshot=<that snapshot> --no-owner --no-acl
  --lock-wait-timeout=60s`. When `pg_dump` exits the session ends, and with it the lock: the
  upload and the checks never keep the hospital from a release or provisioning. `up` and
  `release` stop the dumper before any migration (a dump in flight holds its lock).
- **Sealed:** with `IHR_DUMP_KEY` (Fernet, 1 MiB a token; its own key, not the pgBackRest
  passphrase), uploaded once per name (If-None-Match, Content-MD5), **read back and checked**
  as it arrives (the stored version's size and SHA-256, every token decrypts, the plaintext's
  size and SHA-256, `pg_restore --list` holds that schema only), and only then its manifest.
  After the run each new dump is fetched, checked whole and restored schema-only into a
  throwaway Postgres inside the container (no network). `dumps-check --deep` also has
  `pg_restore` read every data block.
- **Manifests** hold ids, hashes, sizes, the stored versions, fingerprints and sequence values,
  **no personal data** (the public rows travel encrypted in `*.public.enc`). Each names the
  SHA-256 of the hospital's previous one and carries an HMAC. Every reader takes the version a
  manifest recorded, and **any name under `dumps/` written twice or hidden by a delete marker is
  a problem** (DumpNotAuthentic): the dumper never writes a name twice. So a missing, replaced
  or forged dump shows, whoever holds the keys; a new dump written by someone holding the
  server's keys does not (see "Known and accepted").
- **Names:** `dumps/<daily|weekly>/<hospital uuid>/<YYYY-MM-DD>.r<N>.{dump.enc,public.enc,manifest.json}`;
  Sunday's run writes under `weekly/`.
- **Disk:** each dump needs the larger of 0.30 of its schema's bytes on disk and its last
  sealed dump plus a fifth, plus 512 MiB (the read-back streams: no second copy). A dump that
  does not fit beside the one running waits for it; it is refused (`reason=disk`) only when it
  would not fit on the empty volume. The night's check copies one dump at a time.
- **Database role:** `ihr_dump` (`secrets/dumper-db.env`): CONNECT and `pg_read_all_data` only,
  at most 10 sessions (two a dump while `pg_dump` runs, up to 4 at a time, and two more); the
  dumper refuses to run as anything else (`dumps-role`; exit 2: it could not connect, which is
  not a wrong role).
- **Networks:** `internal` (the database, over TCP), `monitoring` (its metrics on :9110 answer
  Prometheus and itself only) and `dumpsout`, its own way out to the bucket. Never `edge`.

**Keys:**

- **Dumper key (server, `secrets/dumps.env`):** add, read and list versions under `dumps/`
  only; never delete, hide, unlock or write elsewhere (`dumps-probe` proves it). On AWS the
  store also refuses any write of it without If-None-Match (`infra/aws`); on a store that does
  not, a rewrite becomes a second version, which `dumps-check` reports (`dumps-probe` says
  which of the two).
- **Dump key `IHR_DUMP_KEY` (server and your password manager):** without it no dump can be
  read, checked or restored.
- **Admin key (your machine only, typed when needed):** for erasure only.

| Command | What it does |
|---|---|
| `scripts/staging.sh dumps-init https://in-south1-objectstore.e2enetworks.net <bucket> Chennai [--uri-style path\|host]` | Once: asks for the dumps bucket's dumper key (typed or two lines on stdin), makes the dump key, writes `secrets/dumps.env` (mode 600) and `IHR_DUMPS=s3`. **Copy `IHR_DUMP_KEY` into your password manager.** `--existing-key`: a rebuilt server continuing the dumps. `--local`: a proof into this stack's SeaweedFS (automatic in `local-proof`; same disk, not a second copy). AWS: `https://s3.ap-south-2.amazonaws.com <dumps bucket> ap-south-2` (`tofu output -json dumper_key`) |
| `scripts/staging.sh dumps-now [--slug S]...` | A run now: every due hospital without a dump since today's 03:30 (or the named ones, again), then the check |
| `scripts/staging.sh dumps-check [--deep [--all]]` | Every manifest and erasure record authentic, each hospital's chain whole, every name under `dumps/` written once, every object present with its size (the version its manifest recorded), the bucket's lock. `--deep`: each hospital's newest dump (`--all`: every dump) read back, decrypted, read whole by `pg_restore`, listed and restored schema-only. Runs from your machine too, without the server (its copies on a Docker volume of its own, removed afterwards: room for the largest sealed dump) |
| `scripts/staging.sh dumps-get --operator <you> --reason "<ticket>" <hospital uuid> <YYYY-MM-DD\|latest> <dump\|public> > file` | One dump (or its public rows, gzip JSON) back, in the version its manifest recorded, checked whole and decrypted (P1-T22b restores from it). Needs disk for the sealed object (a Docker volume, removed afterwards) and for the decrypted file |
| `scripts/staging.sh dumps-probe` | What the dumper key may do and whether the bucket is locked for 30 days with versioning. Run it at go-live; it leaves a small file under `dumps/ihr-key-check/` |
| `scripts/staging.sh dumps-role` | The dumper's database role is `ihr_dump` as modelled |
| `scripts/staging.sh dumps-expire [--dry-run] [--erase-suspect]` | **Weekly, from your machine:** asks for the bucket's admin key and erases every version of nightly dumps older than 14 days and Sunday dumps older than 28, lifting the Governance lock. It refuses shorter periods (`--allow-short` only in a proof on the local stand-in, never abbreviated) and a clock more than an hour off the bucket's, counts an object's age from the younger of its name and its upload, and **first writes a signed erasure record** (`erasure/<time>.json`) so `dumps-check` follows each hospital's chain through what was erased (a record erasing anything inside its retention is a problem). A dump name with more than one version is **SUSPECT** and left alone: look at it (`dumps-check`), then `--erase-suspect`. Run `--dry-run` first |

**Alerts** (`rules/dumps.yml`; log alerts `deploy/logs/alerts/dumps.yml`):

- **DumpTooOld** (critical): a hospital has had no good dump for 26 hours (one never dumped
  counts from when it went live; a held one is not excused).
- **DumpHospitalFailed** (log): names the hospital and why (`ihr-dump: hospital=<slug>
  outcome=failed reason=<lock|disk|window|dump|upload|verify|check|error>`); **DumpHospitalHeld**
  (log) names a held one; **DumpRunFailing**: the last run's count of failures.
- **DumpRunMissing** (critical): no run ended for 26 hours. **DumpRunOverBudget**: a run still
  going after 2.5 hours (06:00 IST).
- **DumpNotAuthentic** (critical): something in the bucket not ours, changed, replaced, hidden
  or unchained, an erasure record erasing inside the retention, or a dump that does not read
  back, list or restore. **DumpBucketNotLocked** (critical). **DumpErasedEarly**: an erasure
  with `--allow-short` (a proof's only), counted whatever its records say; off a proof's own
  stand-in such a record is also checked against the retention like any other (DumpNotAuthentic).
- **DumpRoleRefused** (critical): the dumper refused its database role; nothing is dumped.
- **DumpDiskLow** (less room than `IHR_DUMP_PARALLEL` dumps the size of the largest need),
  **DumpErasureOverdue** (dumps older than 36 days: a weekly `dumps-expire` was missed),
  **DumpDrillOverdue** (no passing restore drill for 35 days, or none 35 days after the first
  dump; P1-T22b), **DumperDown** (not during a release window, which stops it), **DumpsNotReporting**.

The metrics count hospitals only; they never carry a hospital's name or id.

**What each `reason=` means** (then `scripts/staging.sh dumps-now --slug <hospital>`):

| Reason | Meaning and what to do |
|---|---|
| `lock` | Another job (provisioning, the fleet runner, pack apply) held the hospital's lock for 10 minutes. Usually gone by itself; `fleet check` and the provisioner's log say who |
| `disk` | The work volume lacks room for this dump even with nothing else running (DumpDiskLow warns first). Free or grow the volume |
| `window` | The run reached the quiet window (01:45–03:25) before this hospital; the next run takes it |
| `dump` | The snapshot or `pg_dump` failed (its last error is in `logs dumper`) |
| `upload` | The bucket refused or could not be reached; `dumps-probe` |
| `verify` | The uploaded dump did not read back whole (size, SHA-256, every token, the plaintext's SHA-256) or list as that schema alone: no manifest was written. Run `dumps-check --deep`; if it repeats, suspect the bucket or the link |
| `check` | The night's schema-only restore of the new dump failed (`logs dumper` says why) |
| `error` | Anything else, or a hospital row with an unexpected schema name or id |

**At go-live (owner):**

1. In E2E's console, in **Chennai** (the backup region), create a bucket of its own **with
   Object Lock**: Governance, default retention 30 days (a little longer than the 28-day
   retention). Versioning comes with the lock.
2. Create an access key with the **Writer** role on that bucket only; keep the bucket's admin
   key in your password manager (erasure only).
3. On your machine: `scripts/staging.sh dumps-init https://in-south1-objectstore.e2enetworks.net <bucket> Chennai --uri-style path`,
   then copy `IHR_DUMP_KEY` into your password manager. Deploy.
4. On the server: `scripts/staging.sh dumps-probe`. **Every line must PASS; any FAIL blocks
   go-live** (a delete, delete-marker, lock or "write outside dumps/" line: ask E2E for a role
   without it, or use AWS: `infra/aws` makes the bucket and a put-only key). The line "replace
   it without If-None-Match" says whether the store refuses it or keeps a second version for
   `dumps-check` to report; both pass. Then `dumps-now` and `dumps-check --deep`.
5. Weekly on your machine: `scripts/staging.sh dumps-expire --dry-run`, then without
   `--dry-run`.

**Known and accepted:**

- **The dump key is on the server** (as the pgBackRest passphrase and the log archive's key):
  someone holding the server can read dumps, derive the manifest and erasure-record keys and
  write new, authentic-looking dumps. The MACs and the chain catch accidents and outsiders, not
  a compromised server. What protects the dumps already written is the lock, which keeps every
  version for 30 days, and the version-aware readers, which report any name written twice or
  hidden. Per-hospital keys come with P4-T4.
- **Up to 24 hours lost** with a nightly dump; the ≤15-minute path is pgBackRest's
  point-in-time copy (P1-T22b's `--from-pitr`).
- **E2E's Governance lock** stops the dumper's key, not E2E's account admin; the version
  listing still shows a removed, replaced or hidden dump to `dumps-check`.
- **The health check only asks whether the metrics answer.** A failed dump is an alert, not a
  sick container.

## Restoring one hospital (P1-T22b)

Brings **one** hospital back to its last nightly dump (or to a moment, `--from-pitr`) while every
other hospital goes on (no app container is restarted unless you ask: `--restart`). A release or
rollback refuses while a restore is unfinished: finish it (`--resume`) or `--abort` it first. Design: `docs/reviews/p1-t22/design.md` ("After the attack" 4-11 and
"P1-T22b as built"); proof: `docs/reviews/p1-t22/proof-b.md`; code: `ihr_platform/restore/`,
`ihr_audit/archive.py`, `deploy/restore/quarantine.py`.

**What happens** (`scripts/staging.sh hospital-restore alpha --dump latest --reason "..."`):

1. **The dump is fetched and checked** by the dumper's own readers (`dumps-get`, and the manifest
   with `dumper.py manifest`: authentic, chained, its MAC and SHA-256), into `deploy/staging/restore/`
   (mode 700). Free disk is checked first, from the manifest's size; the decrypted dump is removed
   whatever stops the run (an EXIT trap) and as soon as the quarantine has passed.
2. **The quarantine.** A dump is untrusted SQL. It is restored into a throwaway Postgres in the
   dumper image with **no network**, as a role that owns only an empty schema of the hospital's
   name; what came out is checked (only that schema; no function or trigger but ours; no
   extension, role, grant, rule, policy, view or call into another schema; with a nightly dump,
   the audit head, sequences and migrations the manifest names; every default, CHECK and index
   expression calls only pure built-ins and a few others on this schema's own objects). Only then
   is a **package** written from that catalogue: tables, sequences, keys, indexes and rows. The
   quarantine stops its server, checks the files against what it wrote and prints
   `quarantine: package sha256 <hex>`; the restore refuses a package.json with another hash.
   Functions, triggers and the trail's defaults are never taken from a dump: this build's code
   installs them.
3. **`ihr_hospital_restore`** (a logged `migrate` one-off as the owner, the package read-only):
   - **pre-checks** (nothing changes if one refuses): the package passed the quarantine and is of
     this hospital's schema (never a side name); the hospital is active or suspended and not
     already being restored; the dump's audit head is a seal of the live chain; its transaction
     ids are below the cluster's; it is not ahead of this build's migrations; with the same
     migrations, its tables, defaults, constraints and indexes exactly the live schema's; with an
     older build's, its tables have every column the strict merge needs (else refused: restore a
     newer dump); no ABDM data; enough disk; **no erasure ledger** (P4-T1: while that table
     exists, every restore refuses); its lock free (a nightly dump holds it 03:30-06:00: try
     again later);
   - **hold** (a restore hold of its own: the gate answers 503, sign-ins, beat, queued tasks, the
     staff worker, the fleet runner, releases, pack apply and the dumper all leave it alone, and
     a release never lifts it), **drain** (60 s for transactions older than the hold, then the
     hospital's background jobs that began before it: one still running stops the restore here,
     held; `--resume` once it has finished; a job that starts meanwhile is put back at once,
     never kept waiting in a worker), **seal** the live trail and write a fingerprint;
   - **restore**: ONE transaction locks the live trail, seals all of it (the restore's own final
     seal: a long transaction elsewhere or someone holding the sealer's lock cannot leave entries
     only in the parked schema), renames the live schema to `ihr_parked_<schema>_<time>` (no app
     or watch rights), loads the package under the real name (statements one at a time, every
     expression inspected before any is evaluated, its shape and every table's rows checked),
     recomputes the restored chain, advances every sequence past the parked copy, installs the
     trail's guard, defaults, triggers and sealing from this build's code, and merges security
     state strictly: inactive, deleted and unlinked users stay so; organisation, facility and role
     assignments, direct patient grants, patient shares, role permissions, superuser and staff
     flags never stronger than the parked copy's (`--keep-restored-access "why"` keeps the
     restored ones, recorded); every other schema's and public's catalogue must be unchanged
     (a hospital onboarded meanwhile, or one another restore holds, aside: a fleet-wide migrate
     or `db-roles --assert` meanwhile stops the load, `--resume` after it); nothing is half done
     if it stops;
   - **migrate**, the role sync and the packs, in this process under this restore's lock (done
     again by any later step that finds the schema behind this build);
   - **reconcile**: **every staff session invalidated**, the affected patients' access caches
     rebuilt, the hospital's Redis keys deleted; invitations and access requests made after the
     dump, or for users no longer there, cancelled, except a sign-in reset or an invitation's
     withdrawal for a user the restored copy has (kept queued and named: the staff worker runs it
     once the hospital is served); the access difference and the profile's changed fields shown;
   - **keycloak** (run with your bootstrap credential in the unlogged admin one-off): each
     restored active staff link must still be a member of the hospital's organisation, else that
     user is switched off; members with no link (added after the dump) are named. Fail closed;
   - **files**: every object under the hospital's prefix in both file buckets as it was at the
     dump's time, from the buckets' versions (kept 35 days; a server asks for the file buckets'
     admin key, which may list versions); newer uploads are kept and counted;
   - **audit**: the live tail the restore replaced is archived (`ihr_restore_superseded_*` in
     `public`, append-only), a **restore event** goes to the fingerprint bucket, a `restore`
     entry is added to the trail, sealed, fingerprinted, and `ihr_audit_verify --slug` must pass;
   - **restart**: nothing, unless `--restart` (then every running app container at once, so
     every hospital is down for that moment: only for a hospital DatabaseSchemaGated named before
     the restore), then **smoke**: the schema is this build's, triggers and grants as modelled;
     the hold is lifted and the hospital's address must answer 200.
4. The **parked schema** (the hospital as it was just before) is kept 7 days, owner-only, then
   dropped by `hospital-restore-expire` (it holds personal data). **ParkedSchemaOverdue** names
   one kept longer.

| Command | What it does |
|---|---|
| `scripts/staging.sh hospital-restore <slug> --dump <YYYY-MM-DD\|latest> --reason "..." [--dry-run]` | The restore above. `--dry-run`: fetch, quarantine and every pre-check, then stop (nothing held or changed). `--operator NAME` (default: who you are), `--keep-restored-access "why"`, `--restart` |
| `scripts/staging.sh hospital-restore <slug> --from-pitr "YYYY-MM-DD HH:MM:SS+05:30" --reason "..."` | The ≤ 15-minute path (ADR-007): the restore-drill copy at that moment from pgBackRest, the hospital's schema dumped from it, then the same quarantine and restore |
| `scripts/staging.sh hospital-restore <slug> --resume` | Go on after a stop (the database's own state says where; a restart or the Keycloak step is done for you) |
| `scripts/staging.sh hospital-restore <slug> --abort` | Before reconcile only: the restored copy dropped, the live schema un-parked, the hold lifted |
| `scripts/staging.sh hospital-restore-status` | Every restore and its steps; the parked schemas and their age |
| `scripts/staging.sh hospital-restore-expire [--dry-run]` | **Daily (owner):** drops parked schemas older than 7 days (never one of an unfinished restore) |
| `scripts/staging.sh hospital-restore-drill [--slug S]` (or `hospital-restore <slug> --drill`) | **Monthly:** the drill below |

**Exit codes of `ihr_hospital_restore`:** 0 done; 1 refused (nothing changed) or a step failed (the
hospital **stays held**: fix, then `--resume`; or `--abort` before reconcile; a stop before the hold,
e.g. its lock busy, changed nothing there); 3 (only with `--restart`) restart the app containers then
`--resume --restarted`; 4 the Keycloak step needs the operator's credential (a failure there is
reported by the logged one-off that follows, so HospitalRestoreFailed names it).
`hospital-restore` handles 3 and 4 itself.

**After a restore, tell the hospital:** everyone signs in again; work done after the dump's time is
gone from the system (it is in the parked schema for 7 days if it must be re-entered by hand);
access granted after the dump must be granted again (the access difference lists how many);
uploads after the dump are kept in the bucket but not linked.

**The drill:**

- **Monthly** (`hospital-restore-drill`; a 30-day rotation picks the hospital): the newest dump is
  fetched, quarantined and restored **inside the restore-drill copy** of the database (pgBackRest,
  `restore-drill --keep`), never live. The copy runs with `cluster_name=ihr-restore-drill`, and
  `--drill` refuses any other database; the one-off runs with `ihr_platform.settings.drill` (no
  `app.env`, no Redis, storage or fingerprint keys, no Keycloak credential: it refuses to start if
  any reaches it) on a network that holds only the copy (no `edge`). Every step that would reach
  outside the database is a reported dry run. Every other hospital schema and `public` (but the
  drilled hospital's rows) are hashed before and after (rows, triggers, function bodies, grants,
  sequences) and must match. Times are printed; `dumps-drill-mark` records it (DumpDrillOverdue
  after 35 days without a passing one).
- **Quarterly** (owner, with a maintenance notice): a live drill on the synthetic `drill`
  hospital of staging: `ihr_create_tenant --slug drill ...` once, `dumps-now --slug drill`,
  change something in it, then `hospital-restore drill --dump latest --reason "quarterly live
  drill"` and check the result (proof-b.md is the script).

**Alerts** (`deploy/logs/alerts/restore.yml`): **HospitalRestoreFailed** (critical; names the
hospital and the step: it stays held until resumed or aborted) and **ParkedSchemaOverdue**. While
a restore runs, the hospital is also named by HospitalNotServed (it is down, on purpose).

**Database settings:** the restore's one transaction holds a lock on every relation of the
restored schema (about 3.5 per relation, ~3,800 for a CARE hospital today, proof-b.md); the
default lock table (`max_locks_per_transaction` 64 × 100 connections) holds it with little room
to spare; P1-T23 sets it from these numbers. A load that runs out fails cleanly ("out of shared
memory") and rolls back: raise the setting and `--resume`.

**Known and accepted:**

- **Up to 24 hours lost** with `--dump`; `--from-pitr` loses at most what pgBackRest has not yet
  archived (≤ 15 minutes).
- **`ihr_audit_verify` after a restore** must run as the owner or the audit watch's role: the app
  role has no right on the archive (its entries hold values), so run as the app it reads a restore
  event as unverifiable. `release`'s pre-check runs it in the backend: after a restore, run
  `scripts/staging.sh audit-verify --operator <you> --all` instead, which runs in the audit watch's
  container (its role, and the fingerprint reader key; monitoring must be on, else it falls back to
  the backend) (proof-b.md; the admin one-off of `manage` holds no reader key, on purpose, so
  `ihr_audit_verify` there always exits 1: P1-T27 live proof F5).
- **Queued Celery tasks** of the hospital made before the restore run after it (the gate holds
  them while it is held); ones naming rows the restore removed fail harmlessly.
- **Offboarding** must also erase the hospital's archived segments (append-only; P4-T1/§7.7).
- **A dump of another cluster** (a move) is refused: it needs a rebase seal (not built).


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
| Availability | PublicEndpointDown (auth, status; 3 min), TargetDown (5 min; critical for traefik, keycloak, postgres, redis, s3, node), PostgresDown / RedisDown (the exporter answers but cannot reach its server or log in; 5 min), BackendHealth, KeycloakNotReady, ContainerNotRunning (critical for the hospitals' services and `celery-worker`/`celery-beat`, warning for `provisioner`, `staff-worker` and monitoring; the four workers not until the window's end while a release has them stopped, see "Releases"), ContainerUnhealthy, ContainerRestarting (more than 2 in 15 min) |
| Capacity | DiskFreeLow (20 GB warning, 12 GB critical on `/`; other data mounts also below 25 % / 15 %; `/boot` never), DiskFillingUp (below 12 GB within 7 days), MemoryLow (10 % / 5 %), OomKill, SwapInUse, ContainerMemoryNearLimit (90 %), CpuLoadHigh, JournalNearCap, JournalSuppressed (critical since P1-T27a: dropped lines can hide database sessions and operator lines), PgConnectionsHigh (80 of 100), PgLongTransaction (1 h), RedisMemoryHigh (80 % / 90 %), RedisPersistenceFailing, CeleryBacklog (200 for 10 min) |
| Backups | BackupUnhealthy, BackupTooOld (26 h), BackupCheckStale (2 h), WalSpoolStuck (10 min), WalSpoolNearCap (75 %), WalArchiveFailing |
| Audit trail (P1-T21) | AuditChainBroken, AuditFingerprintMismatch, AuditTriggerDisabled (all at once), AuditTriggersMissing (30 min), AuditFingerprintLate (2 h 15 min / 6 h), AuditSealLag (15 min / 1 h), AuditSealHeldBack, the fingerprint store's problems by class, and the watch's own staleness: see "Audit watch" |
| Operator access (P1-T27a) | PostgresSettingOverridden (critical: a setting stored by ALTER SYSTEM in postgresql.auto.conf), PostgresSettingCheckFailing (15 min): see "Operator access record (P1-T27)" |
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

**Reference packs** (`deploy/logs/alerts/packs.yml`, P1-T15a). The start-up one-shot applies the
packs (CARE's system value sets first) to every live hospital (`ihr_fleet_run ... packs
--startup`, which applies them exactly as `ihr_pack_apply --all --startup` does). One hospital's
failure never stops the start: it is recorded in that hospital (a `PackApply` row with the error)
and logged as `ihr-packs: hospital=<slug> outcome=failed`.

**Hospitals not served** (`deploy/logs/alerts/fleet.yml`, P1-T18a). After each fleet run
(`ihr_fleet_run`: the start-up one-shot, or an operator's run with `scripts/staging.sh fleet`),
every live hospital that is held or whose schema is not this build's, and every hospital a
step of the run failed for, is logged as `ihr-fleet: hospital=<slug> outcome=failed|deferred|held`;
the worker repeats the line every minute while a live hospital stays unserved, so the alert
does not clear while it is down. A provisioning or offboarding hospital is not served by design
and is never named. That hospital answers 503 "upgrading" while the rest work. Its newest
`upgrade` operation names the step and a safe error; fix the cause, then
`scripts/staging.sh fleet --resume <fleet run id> --operator <you>` (a hospital held only by a
refused role sync is released by that resume once its roles pass). A hospital held on purpose
(a window) is served again with `scripts/staging.sh fleet release --slug <it> --operator <you>`.
Always run the fleet runner with `staging.sh fleet`, never `staging.sh manage`: the admin
container logs nothing, so its lines would never reach the alert. One fleet run at a time; a run
killed half-way is closed as interrupted by its `--resume`. A run that takes hospitals' locks
(`migrate`, `roles`, `packs`, a `--resume`) stops a running nightly dumper first and starts its
container again afterwards, whatever the outcome: a dump holds its hospital's lock while
`pg_dump` runs, and the fleet waits only about 50 s for a busy hospital (a dump it cut short is
redone by its catch-up run).

| Alert | When | Severity |
|---|---|---|
| PackApplyFailed | exactly that whole line, from the `migrate` one-shot (dev: `celery`), in the last 24 hours (the mail names the hospital; request paths in the web backend's log can never raise it) | warning |
| PackFacilityDeferred | exactly `ihr-packs: hospital=<slug> outcome=deferred facility=<external id\|unknown>`, from the workers alone (`celery`, `celery-worker`), in every 40-minute window for two hours: a facility the per-facility packs could not reach (see "Per-facility packs" below) | warning |
| HospitalNotServed | exactly the `ihr-fleet:` line above (outcome failed, deferred or held), from the `migrate` container (the one-shot and `staging.sh fleet`) or the worker's per-minute report (`celery-worker`; dev: `celery`), in the last 24 hours | critical |
| AuditHospitalCheckFailed / AuditHospitalSealLate | exactly the audit watch's `ihr-audit:` line (chain, fingerprint, triggers or unchecked / seal), from the `audit-watch` service alone, in the last 30 / 10 minutes (see "Audit watch") | critical / warning |
| AuditEventRecorded | exactly the audit watch's `ihr-audit: hospital=<slug> event=<restore\|offboarding> outcome=recorded` line, once per new event, in the last hour | warning |
| AuditWholeRestoreRecorded | exactly the audit watch's `ihr-audit: restore=<id8> event=whole-database hospitals=<n> outcome=recorded` line, once per whole-database restore's result, in the last hour (P1-T22c) | warning |

Fix the cause, then `scripts/staging.sh manage --reason "<ticket>" --hospital <slug> ihr_pack_apply --slug <slug> --operator <you>`
(`<you>`: 1-64 letters, digits or `. _ @ -`; recorded as `packs:<you>`).
A busy hospital (another job holds its lock) is tried again after 5, 15 and 30 seconds; still
busy after that, it is recorded (`outcome=deferred`) and alerted on like a failure.
A data check that hit its cap (P1-T15b2a: identifier configs) has no `PackApply` error: the
line before the alerted one reads `ihr-packs: hospital=<slug> pack=<pack> item=<item> data check
not fully checked`, and it comes back at every apply until the check is finished with
`scripts/staging.sh manage --reason "<ticket>" --hospital <slug> ihr_pack_pending --slug <slug> --recheck --operator <you>` (it takes
the same hospitals as `ihr_pack_apply --slug`; `ihr_pack_pending --slug <slug>` lists what holds
each item: a conflict, the counts, whether fully checked, and when).

**Per-facility packs** (P1-T15b3, `ihr_refdata/tasks.py`). A per-facility pack (a facility's own
catalogue rows) is applied in every live facility, each with its own records. A new facility
queues one Celery task for its hospital once its creation commits; the task fills only the
facilities that lack the current release, never a hospital-wide pack, under the hospital's lock.
A busy hospital is tried 3 times (30 s, 2 min, 5 min); then the worker writes
`ihr-packs: hospital=<slug> outcome=deferred facility=<external id>` and leaves it to the
**backstop**, which beat runs every 15 minutes in each active hospital: it finds, in one query,
the live facilities without the current release (a give-up, a facility restored after deletion)
and writes the same line at every run it still cannot fill one. PackFacilityDeferred mails after
two hours of such lines. `facility=unknown` means the worker refused the pack files themselves.
The workers write no `PackApply` row for a failure: read their `ihr-packs:` log lines, fix the
cause, then `ihr_pack_apply --slug <slug> --operator <you>` reconciles every live facility.
Neither the task nor the backstop touches a held, restoring or suspended hospital, nor a release
the start-up has not recorded yet. After an image roll-back to an older pack version, the
facilities wait (a warning line, no alert) until the roll-forward.

An item that links to another (an activity to its specimens) waits as `missing-link` while a
target is missing or deleted in that facility: nothing is written, accept is refused, and the
apply or the backstop writes it once every target is live again. Decide a per-facility item with
`ihr_pack_pending --slug <slug> --accept|--keep <pack>:<item> --facility <external id> --operator
<you> --reason "<why>"` (the external id as the list prints it; `--facility` without a decision
lists only that facility's items). A deleted facility's items are listed with `(deleted)` and
cannot be decided until it is restored.

**Batch requests** (P1-T20a, `deploy/logs/alerts/batch.yml`): the web backend runs a sub-request
of `/api/v1/batch_requests/` only if its method and route are on the list the web app's own batch
call sites generate (`batch_allowlist.txt`); anything else refuses the whole batch, nothing runs.

| Alert | When | Severity |
|---|---|---|
| BatchRequestRefused | `ihr-batch: refused hospital=<slug> reason=<code> method=<METHOD> route=<URL name>` from the web backend, in the last hour: the web app batched a route the list lacks (regenerate it with `scripts/batch-allowlist.py`, review, release) or someone sent a batch by hand (the access log of that minute names the address) | warning |

**Records grants** (P1-T11b4, `deploy/logs/alerts/records.yml`; Q-K36 A): MRD reads one
patient's clinical record at a time, for a reason code, for 30 minutes. The web backend writes
`ihr-records: hospital=<slug> grant=<id|none> user=<id|none> reason=<code|invalid|none>
outcome=<granted|ended|expired|refused> hour=<IST hour>` (ids only: the patient is found from the
grant id in the hospital's audit trail).

| Alert | When | Severity |
|---|---|---|
| MrdRecordsRefused | one staff user refused 3 or more times in 10 minutes (no right, a wrong reason or reference, no such patient) | warning |
| MrdRecordsVolume | one staff user given more than 40 grants in 24 hours | warning |
| MrdRecordsOffHours | a grant between 22:00 and 07:00 India time (mailed at once) | info |

Runbook: as that hospital's admin, `/api/ihr/audit/entries/` shows each read with purpose
`records` and its grant ids; `DELETE /api/ihr/records-access/<id>/` ends a live grant at once (a
hospital-wide admin may end anyone's). The thresholds are the numbers in `records.yml`.

**Database roles** (P1-T19a, `deploy/logs/alerts/db-roles.yml`; `IHR_DB_ROLES=required` on
every app container since P1-T19b, see "Moving to split roles"):

| Alert | When | Severity |
|---|---|---|
| DatabaseRoleRefused | `ihr-db-role: refused role=<app\|owner\|none> reason=<code>` from a service that runs as a declared role, in the last 30 minutes: that container serves nothing | critical |
| DatabaseSchemaGated | `ihr-db-role: gated schema=<schema> reason=<extra_privileges\|controls>` from an app service (the worker repeats it every minute), in the last 15 minutes: that hospital schema is not served, and the shared app role may still hold the right | critical |
| DatabaseRoleLoginFailed | Postgres's own `password authentication failed for user "<ihr_app\|ihr_owner\|...>"`, from the `db` container, in the last 10 minutes: a split role's password may have been changed by whoever could log in as it | critical |
| DatabaseStatementTimeouts | Postgres's own `canceling statement due to statement timeout`, `... due to lock timeout` (a request blocked by a held lock is cut off by the 10 s lock limit first) or `terminating connection due to idle-in-transaction timeout`, from the `db` container, more than 5 times in 10 minutes (P1-T23: the limits of `ihr_platform/db_limits.py`, web 30 s statement and 60 s idle in a transaction, workers 300 s): look for what holds locks or sits idle in a transaction (`pg_stat_activity`), and which request or task was cut off, before raising any limit | warning |

Runbook: `manage.py ihr_db_role_check --report` in the container names every finding (a
refused container exits before its process starts and restart-loops: `docker compose logs
<service>` shows the lines before the refusal). Grants and controls: a fleet-wide
`scripts/staging.sh fleet migrate --operator <you>` (no `--slug`: it repairs every hospital
schema, archived ones too), then restart the app containers (`scripts/staging.sh up`). The roles
themselves (a password: DatabaseRoleLoginFailed; stored settings, `reason=role_settings`, which
anyone holding the app role can cause; a membership or an attribute): `scripts/staging.sh
db-roles --assert`, which as postgres sets both passwords again from the secrets, resets every
stored setting of every role of the model **in every database** (`ALTER ROLE ... RESET ALL` and
`ALTER ROLE ... IN DATABASE <each> RESET ALL`: a role may store a default for itself in
`postgres`, `template1` or `keycloak` too, and the refusal counts every one; its text names the
databases) and revokes any membership; then `scripts/staging.sh up`. A large object the app owns
(`reason=owns_objects`): as postgres, keep a copy for the incident, then `SELECT lo_unlink(oid)
FROM pg_largeobject_metadata WHERE lomowner = 'ihr_app'::regrole`. Treat each of these as a sign
the app role was used by someone else.

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
| `scripts/staging.sh logs-archive-get --operator <you> --reason "<ticket>" <YYYY-MM-DD> [revision] > day.jsonl` | One day back as JSON lines (the latest revision unless one is named), checked and decrypted; a day's revisions are listed on the terminal. For an incident report to CERT-In older than the store's 180 days |
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

## Audit watch (P1-T21, ADR-010)

The `audit-watch` container (`compose.monitoring.yaml`, on with the monitoring) checks every
hospital's audit trail and tells Prometheus what it found (design and numbers:
`docs/reviews/p1-t21/design.md`, "As built"). It runs `manage.py ihr_audit_watch serve` from the
backend image with its own settings (`ihr_platform.settings.watch`: no app secret, read-only
database sessions), its own read-only database role `ihr_audit_watch` (`secrets/audit-watch.env`),
the fingerprint bucket's **reader** key only, and its own way out to the bucket (`auditout`).
Its checkpoints and state are in its own volume (`auditwatch`), never in the database.

| Pass | When | What |
|---|---|---|
| fast | every 60 s | per hospital: finished entries waiting unsealed, entries held back by a running transaction; every hospital schema: any of the trail's triggers switched off |
| incremental | every 15 min | restore and offboarding events; fingerprints since yesterday; per hospital the header walk of every seal, the chain from its checkpoint (the entries of every newer seal recomputed) and the fingerprints' claims; trigger coverage for servable hospitals |
| nightly | 04:00 IST (an hour later again after a failure) | the entries of every seal of the last 30 days recomputed (budget `IHR_AUDIT_WATCH_REHASH_BUDGET`, time limit `IHR_AUDIT_WATCH_REHASH_MAX_SECONDS`, 45 minutes; horizon `IHR_AUDIT_WATCH_REHASH_DAYS`). Where the 30 days start is the watch's own daily record of each hospital's head, never only the database's `sealed_at`, which the owner could back-date |
| sweep | in the nightly pass, once a week (at the next nightly again after a failure) | every fingerprint ever written |

It names a hospital only in one fixed line on its log,
`ihr-audit: hospital=<slug> check=<chain|fingerprint|triggers|seal|unchecked> outcome=failed`
(`invalid-slug` for a slug off its pattern), which the log alerts below take from the
`audit-watch` service alone; the metrics count hospitals. Each hospital is checked on its own:
one it cannot read (a lock held past its 10 s lock timeout, a statement over 5 minutes, a
revoked grant) is **unchecked**, which pages, never clean, and the others are still checked. A
hospital a release holds (`ihr_fleet_run`: the workers are stopped and its migration may lock the
trail) is the exception for two hours from when the watch first saw the hold: its seal lag is not
judged and a check it cannot finish is deferred (`ihr_audit_hospitals_held`); after that it is
judged as any other. A pass counts as run only when it ends without an error
(`ihr_audit_watch_last_success_timestamp_seconds`).
Its metrics answer only Prometheus and itself, a few requests at a time.

It needs the fingerprint storage: with monitoring on and an empty `secrets/anchor-read.env`, `up`
and `release` refuse before they change anything (run `s3-init`, or fill it for a provider
bucket), and `scripts/deploy.sh` before it sends anything (`staging.sh watch-reader --check` on
the folder it sends).

Look at a hospital yourself: `scripts/staging.sh audit-verify --operator <you> --slug <slug>` (from seal
1), or in the watch's container `python manage.py ihr_audit_watch once --pass incremental
--metrics`: it reads the serving watch's state and never writes it, and counts the nightly and
sweep findings that stand until the next nightly apart (`standing_findings`). `ihr_audit_watch
measure` is bounded (the last 7 days, 200,000 entries a hospital); `--full` runs only on a
`bench_*` database or the drill's copy.

| Alert | When | Severity |
|---|---|---|
| AuditChainBroken | a hospital's chain fails (a changed, missing or added entry; a cut, rewritten or moved seal; its checkpoint gone) | critical, at once |
| AuditFingerprintMismatch | a hospital no longer matches its outside fingerprints | critical, at once |
| AuditTriggerDisabled | one of the trail's triggers is switched off in a hospital schema | critical, at once |
| AuditTriggersMissing | a servable hospital lacks triggers or has changed audit functions, for 30 min | critical |
| AuditFingerprintLate / AuditFingerprintNeverRead | the newest fingerprint is over 2 h 15 min old (warning) or 6 h (critical); none read for 3 h | warning / critical |
| AuditFingerprintsUnreadable / AuditFingerprintBucketWeak | the reader fails for 1 h / the bucket's lock is weaker than required for 30 min | warning / critical |
| AuditFingerprintObjectUnreadable, ClockSkew, ObjectRewritten, UnknownHospital, AuditEventInvalid | an object in the bucket that is not ours, says a wrong time, was written twice or hidden, names a hospital the database has lost with no offboarding event, or a bad event | critical |
| AuditSealLag | finished entries waited over 15 min (warning) / 1 h (critical) with no new seal, for 5 min | warning / critical |
| AuditSealHeldBack | entries held back over 1 h by a long transaction (see PgLongTransaction) | warning |
| AuditCheckpointLost | a checkpoint was seeded again (once on a first start; otherwise its volume was lost); a hospital made after the volume is not counted | warning |
| AuditCheckpointUnseeded | no checkpoint, and none can be seeded because the fingerprints are unreadable, for 1 h | warning |
| AuditHospitalsUnchecked | the watch could not read a hospital: seal lag for 5 min, the chain two passes in a row (critical); the nightly (warning) | critical / warning |
| AuditHospitalsRestoring | a hospital held for a restore (its chain is not checked) for 6 h / a day | warning / critical |
| AuditHospitalsNotComparable | live hospitals held or not on this build's schema for a day (their triggers are not judged) | warning |
| AuditWatchFailing (fast, incremental) | the every-minute pass fails for 5 min; the chain pass twice in a row | critical |
| AuditWatchStale / Absent, FullCheckStale / Absent, Failing (nightly, sweep), AuditRehashOverdue | a pass has not ended without an error (fast 5 min, incremental 45 min, nightly 26 h, sweep 8 days, or never), the nightly or sweep fails for 30 min, or the 30-day recompute did not complete for two nights (the alert names the limit that ended it) | warning |
| AuditHospitalCheckFailed (log) | the watch's line for chain, fingerprint, triggers or unchecked, in 30 min; the mail names the hospital | critical |
| AuditHospitalSealLate (log) | the watch's line for seal, in 10 min; the mail names the hospital | warning |
| AuditEventRecorded (log) | a new restore or offboarding event in the bucket, once; the mail names the hospital | warning |
| AuditWholeRestoreRecorded (log) | a whole-database restore's result in the bucket, once; the mail names the restore id's first 8 digits and the number of hospitals (P1-T22c) | warning |
| AuditDatabaseRestoredUnannounced | the database's timeline changed and no whole-database restore's result explains it (`ihr_audit_timeline_unexplained`: the number of changes the watch keeps that none explains; P1-T22c) | critical |
| AuditRestorePlanAbandoned | a whole-database restore's plan has had no result for a day and the database is still on its timeline (a rollback stopped before its restore and was not run again; `ihr_audit_restore_plans_abandoned`) | warning |
| AuditWholeRestoreUnanchored | a whole-database restore's result the timeline chain does not anchor: its events excuse nothing (`ihr_audit_whole_restores_unanchored`) | warning |
| AuditWholeRestoreUncorroborated | a whole-database restore's result whose timeline change the watch's own history never saw (`ihr_audit_whole_restores_uncorroborated`): confirm its restore id against `releases.log` | warning |
| AuditWholeRestorePending | a disaster restore (`restore --target`) served before its audit load: its stub plan has no result (`ihr_audit_whole_restores_pending`, from the plans alone; P1-T22c2a). Every hospital sealed after T reads as cut until `audit-supersede export <id>` and `load <id>` | warning |
| AuditWholeRestoreClosed | a whole-database restore was closed without loading its audit tails (`audit-supersede close`, or `restore --target --no-supersede`) and not acknowledged (`ihr_audit_whole_restores_closed{how}`; P1-T22c2a, Q-K105): data written after the last fingerprint can be lost with no trace. Review it, then list its restore id in `ihr_audit/acknowledged_closed.txt` | warning |
| AuditWholeRestorePendingOverdue | that load has waited more than 48 h (`ihr_audit_whole_restore_pending_oldest_seconds`, from the stub's stored time): load it, or `audit-supersede close <id> --reason` (the cuts stay) | critical |

**Runbooks.**

- **AuditChainBroken / AuditFingerprintMismatch / AuditHospitalCheckFailed.** Treat it as a
  possible incident (CERT-In: report within 6 hours). Run
  `scripts/staging.sh audit-verify --operator <you> --slug <the hospital named>`: it names the seal,
  the entry window or the fingerprint. Do not "fix" the trail: it is evidence. A checkpoint is
  never reset by hand. After a genuine restore the restore's event clears it (P1-T22); a restore
  without one stays a cut. **`staging.sh rollback --restore`** writes one per hospital it rolls
  back (P1-T22c, "Whole-database restores" below); **`staging.sh restore --target`** (a disaster
  restore) writes them after its export and load (P1-T22c2a, "Disaster restores" below): until
  then every hospital sealed since its target reads as cut, and claims sealed after the
  repository's last WAL stay cut (P1-T22c2b). Run it only for a real incident, record it as one,
  and never silence the alerts.
- **AuditHospitalsUnchecked / a `check=unchecked` line.** The watch cannot read that hospital's
  trail: `scripts/staging.sh logs --operator <you> --reason "<ticket>" audit-watch` names the error. A lock held past 10 s (a
  migration, or someone holding the trail's tables), a statement over 5 minutes (a very long
  chain), or a grant of `ihr_audit_watch` taken away (`up` puts them back). Treat a hospital that
  stays unchecked as failing its check.
- **AuditHospitalsRestoring.** A hospital held for a restore (P1-T22) is not chain-checked until
  the hold ends (its earlier findings still show). Finish the restore or lift the hold; a hold
  nobody runs a restore for is a way to hide a trail.
- **AuditCheckpointUnseeded.** The fingerprints cannot be read (AuditFingerprintsUnreadable) and
  the watch has no checkpoint for some hospitals: it never seeds them from seal 1 blindly. Fix the
  reader; the next pass seeds them from the newest fingerprint.
- **AuditEventRecorded.** Confirm the restore or offboarding was yours. If not, the events key
  (or, on a site without one yet, the add-only writer key `secrets/anchor-write.env`, which Celery
  holds) was used: an incident. An
  offboarding event closes a hospital only if the watch saw it offboarding or archived and its
  final seal is the newest fingerprint's; otherwise it is a bad event (AuditEventInvalid) until
  you review it and list its object key in `acknowledged_objects.txt`.
- **AuditTriggerDisabled / AuditTriggersMissing.** Writes there leave no trace while it lasts. A
  migrate puts every trigger back (`scripts/staging.sh fleet migrate --slug <slug> --operator <you>`);
  find who switched it off (the database's own log has no DDL: look at who held the owner's
  secret).
- **AuditSealLag / AuditHospitalSealLate.** An active hospital: celery-beat and celery-worker
  (ContainerNotRunning, CeleryBacklog). A suspended, offboarding or archived one: the
  `ihr_audit.seal_inactive` job in celery-worker's log. `ihr_audit_verify --slug <slug> --seal`
  seals it by hand.
- **AuditSealHeldBack.** A transaction has been open for over an hour somewhere in the cluster
  (PgLongTransaction names it); sealing resumes when it ends.
- **AuditFingerprintLate.** The hourly job runs in celery-worker (`ihr_audit.anchor`); its log
  says why it fails (the writer key, the bucket). Everything sealed since the newest fingerprint
  is unprotected against the database owner until a new one is written.
- **AuditFingerprintsUnreadable.** The reader key (`secrets/anchor-read.env`), the network
  `auditout`, or the storage. **AuditFingerprintBucketWeak:** the bucket's Object Lock or
  versioning was changed (`scripts/staging.sh s3-check`).
- **A store problem** (an unreadable, skewed, doubled or hidden object). The bucket is
  write-once for years: the object can never be removed. Find it in the watch's log
  (`fingerprint <key>: ...`), decide what it is, and once reviewed add the object key to
  `plugins/ihr_platform/ihr_audit/acknowledged_objects.txt` with the date and the reason (one
  whole key per line, never a pattern); it then no longer counts, and the next one alerts again.
- **AuditCheckpointLost.** Expected once after the first start. Otherwise the watch's volume was
  wiped or replaced, or a checkpoint file removed: the checkpoints were seeded from the newest
  fingerprint (never the database), so a rewrite since then is still caught; find out who removed
  it. A hospital made after the volume is not counted; each hospital is counted once per start.
- **AuditWatchStale / Absent / Failing.** `scripts/staging.sh logs --operator <you> --reason "<ticket>" audit-watch`. A stuck pass does
  not stop the others (each has its own thread). **AuditRehashOverdue:** the 30-day horizon no
  longer fits a night; the alert's `limit` label says which limit ended it
  (`ihr_audit_rehash_stopped_by`; `ihr_audit_rehash_rate_entries_per_second` is the rate it
  measured). `budget`: raise `IHR_AUDIT_WATCH_REHASH_BUDGET` in `.env`. `time`: raise
  `IHR_AUDIT_WATCH_REHASH_MAX_SECONDS` (well under 26 hours), or shorten
  `IHR_AUDIT_WATCH_REHASH_DAYS`, which narrows the promise (an edit older than that is found only
  by the monthly drill).

**Restores and offboarding.** P1-T22's restore and an offboarding write one event each into the
fingerprint bucket (`ihr_audit/events.py`, with the add-only key: `write_restore` after keeping the
superseded segment, `write_offboarding` with the deletion certificate before the schema goes).
Without one, the watch reports the rollback as a cut and the deleted hospital as unknown, as it
must. Order for an offboarding (TENANCY §7.7): seal, let the hourly fingerprint name the final
seal, write the event with that seal, then drop the schema and delete the hospital's row in one
transaction. Each new event is mailed once (AuditEventRecorded); a hospital held for a restore
counts in AuditHospitalsRestoring until the hold ends.

**Disaster restores (P1-T22c2a; design `docs/reviews/p1-t22c/design-c2.md`, "c2a as decided").**
The old database is gone; the hospitals are served first, the audit tails loaded afterwards:
1. `scripts/staging.sh restore --target 'YYYY-MM-DD HH:MM:SS+05:30' --operator <you>`. Needs the
   backend image on this server, a build of P1-T22c2a or later and the owner's **events key**
   (`secrets/anchor-events.env`, or `IHR_AUDIT_ANCHOR_EVENTS_KEY/_SECRET` in your shell); each is
   refused before anything is replaced, naming the way out. Just before the restore it writes a
   **stub plan** (restore id, T, the repository's newest timeline and backup set, no hospitals)
   and `deploy/staging/supersede/<id>/state`; after recovery it records the new timeline and its
   parent (the history file), and the restore's own stop (the first commit after T, not applied)
   and the last commit it applied: the export doc refuses unless T lies between them (Q-K104). A
   parent that is not the stub's timeline cannot be exported: `audit-supersede close <id> --reason`.
   An attempt that fails after its stub is marked `abandoned` by itself (`up` stops naming it);
   run `restore --target` again. Its stub is never closed (refused: it never promoted); the next
   attempt's result supersedes it. A closed attempt never supersedes a later one (review E3/Q1).
2. `up`, `smoke`, `backup-now full`. Until step 4 every hospital sealed after T reads as cut
   (AuditChainBroken, critical) and **AuditWholeRestorePending** (warning) names the restore; after
   48 h **AuditWholeRestorePendingOverdue** (critical). Both come from the plans alone, so a new
   server's audit watch reports them on its first pass.
3. `scripts/staging.sh audit-supersede --operator <you> --reason "<ticket>" export <id>`: a copy of the database from
   the repository (backup set and old timeline to its end; container `ihr-supersede-copy`, 768 MB,
   killed first under memory pressure, the dumper stopped meanwhile; disk = the set's size + 3 GB),
   read by the audit watch's role; then the **export doc** (events key). The copy goes on every
   exit. A hospital that cannot be read refuses the export unless
   `--accept-unreadable --reason "<why>"` (it stays cut). The export warns 14 days after the stub
   (the backup set leaves the repository after 28 days). A second export, or any other whole-
   database export or restore while this one is not loaded, is refused.
4. `scripts/staging.sh audit-supersede load <id> --operator <you>`: per hospital the fork point is
   the restored head at T, never chosen; it is refused if the first superseded seal holds no entry
   newer than T − 15 min (Q-K99: a rewritten restored seal). A hospital made after T is `absent`
   (it keeps alerting until P1-T22c2b closes it). Then the result; the local `export.tar` is
   deleted unless it holds unsealed entries or an unreadable hospital (then kept, mode 600).
A restore that cannot be loaded: `audit-supersede close <id> --reason` (Q-K100; the cuts stay).
`restore --target ... --no-supersede --reason "<incident>"` writes the stub and that result at once.
A closed restore (either way) explains the timeline change but can lose, with no trace, what was
written after the last fingerprint: **AuditWholeRestoreClosed** (warning, gauge
`ihr_audit_whole_restores_closed{how}`) and `ihr_audit_verify` name it until its restore id is
listed in `plugins/ihr_platform/ihr_audit/acknowledged_closed.txt` with the date and what was
reviewed (Q-K105). Its local `export.tar` is kept (it is the only copy of the old tail; `up` and
`audit-supersede status` say so); its expiry rule is P1-T22c2b's, with Q-K90. `close` and `load`
refuse to start the database over an empty or half-restored volume: restore first.
**A second disaster, a restore not yet loaded.** A pending restore from an older timeline than the
repository's newest does not block the next `restore --target` (a lost server can neither load nor
close it), and its export is not blocked either; it keeps alerting as pending until its own result.
`restore --target` right after a restore, before the next backup, is refused (no backup set on the
repository's newest timeline yet): `backup-now full` first, or restore with `--target-name`. A
`export` that stopped after the export doc was written finds it and says to load. The 14-day warning
(step 3) is on the export.
Claims sealed after the repository's last archived WAL stay cut until P1-T22c2b.

**Whole-database restores (P1-T22c1; design `docs/reviews/p1-t22c/design.md`).** A
`rollback --restore` puts every hospital and the platform back to the window's restore point T;
the fingerprints stored after T stay in the bucket. So the rollback, after you typed the tag:
1. holds every hospital (`ihr_fleet_run hold`, by the build being left; a rollback that stops here
   marks its holds in `releases/rollback-hold`, which a second `rollback --restore` accepts), stops
   the workers and the dumper, drains, seals and writes a fingerprint (best effort, as a release);
2. exports each hospital's audit tail after T (`audit-supersede` one-off: the audit watch's
   read-only role and the reader key; one tar `deploy/staging/supersede/<id>/export.tar`, mode 600
   in a 700 folder: audit entries are personal data, kept beside the copy of the database);
   T's lower bound per hospital is the newest fingerprint stored at or before the restore point
   (the release's own fingerprint of its sealed head). A hospital whose first seal after that
   fingerprint was sealed before T (the release's seal did not run: a stale fingerprint), or whose
   database records fewer restore generations than the bucket, is refused here by name, before
   anything changes; then the `--accept-absent` check on the export;
3. writes the **plan** (`anchors/ihr-restores/<suffix>/<id>/plan.json`, write-once) from the
   owner's one-off with the **events key**; only then `down`, the copy, the restore;
4. starts the database alone and **loads** (`ihr_audit_supersede load`, the owner, before `up`):
   per hospital the restored chain must end exactly at T's lower bound (F = L; else refused,
   named), the tail is archived as the next restore generation (the bucket's count of restore
   events + 1, the database's count must agree), an ordinary restore event is written (with
   `cause: whole-database` and the restore id) and a `restore` entry added to the trail; archive
   generations the restore took away are loaded back as they were; then the **result** with the
   new timeline. Logged in `releases.log` as `supersede=<id>`.

The watch then mails one AuditEventRecorded per hospital and one AuditWholeRestoreRecorded:
confirm the restore id is the one in `releases.log`, and run
`scripts/staging.sh audit-supersede status <id>` (per hospital: planned, outcome, seals, generation,
any problem). This build also requires, beyond the archived segment, a plan stored before the event,
a result naming it, the plan's lower bound read again from the fingerprints, and the result's new
timeline being the database's now (older restores through the chain: each plan's old timeline is
the previous result's new one). The watch records the database's timeline each pass; a change no
result explains raises **AuditDatabaseRestoredUnannounced** (critical; every change the watch
keeps is judged, not only the latest). Three warnings follow the restores: **AuditRestorePlanAbandoned**
(a plan with no result for a day while the database is still on its timeline),
**AuditWholeRestoreUnanchored** (a result the timeline chain does not anchor: its events excuse
nothing; `ihr_audit_verify --all` says why) and **AuditWholeRestoreUncorroborated** (a result
whose timeline change the watch's own history never saw, e.g. a new watch volume: confirm the
restore id against `releases.log`). The rollback's target build
(older than P1-T22c) accepts the events on the segment rule alone.

- **A rollback that stopped.** Before the restore (stage 5: held, exporting): the database and
  the images are unchanged; its message says whether the hold took. If the plan was already
  written it **stays in the bucket** (write-once): run `rollback --restore` again (its new plan,
  from the same timeline, supersedes the old one once it has its result), or `up` then
  `fleet release` (which also ends the rollback's `releases/rollback-hold` marker) and record the
  stopped rollback: the watch then reports the plan after a day (AuditRestorePlanAbandoned,
  warning). During or after the restore, run
  `scripts/staging.sh audit-supersede load <id> --operator <you>` before `up` (`up` warns while one
  has no result); it is safe to run again (each step is checked before it is done), also after
  `up` and `fleet release`: the restored chain is read at T's lower bound, not at its head, so
  seals added since do not refuse it. One load runs at a time (a second one is refused).
  After a rollback to a build older than P1-T22c1 on a local gateway (`IHR_S3=local`), its `up`
  recreates the gateway without the events identity: run the load before `up`, as the rollback
  does (a load after it needs the events key unset, so the writer's key adds the events).
- **Hospitals made after T** are gone after the restore and keep alerting (unknown hospital) until
  offboarded by hand: the rollback refuses without `--accept-absent` (P1-T22c2 closes them).
- **The events key** (Q-K91) is made by `s3-init` (a proof: `secrets/anchor-events.env`;
  `s3-events-key` adds one to an older proof folder) and is the owner's: `deploy.sh` never sends
  `anchor-events.env`, and no app container's env holds it; on a server export
  `IHR_AUDIT_ANCHOR_EVENTS_KEY`/`_SECRET` in your shell for the rollback and for
  `hospital-restore` (its event goes with the events key too). **Limit:** with this folder's own
  gateway (`IHR_S3=local`) the gateway must know the key to check it, so its identity file
  `secrets/s3-gateway.env` (sent by `deploy.sh`, with the admin key it already holds) carries the
  events key's secret on the server's disk; Celery still never sees it. A provider bucket keeps
  it in the provider's IAM only.
  With it the gateway lets Celery's writer add fingerprints only (`anchors/<suffix>/`). **Until a
  site has it** (and on a provider bucket until its events user exists), the events go with the
  writer's key, which Celery holds: the plan, result and timeline checks are then defence in
  depth, not a security boundary; the archived-segment rule (the original must be kept) still is.
- `scripts/staging.sh restore --target` (a disaster restore) has no plan yet (P1-T22c2).

**Proven on the local staging stack (P1-T21):** a seal rewritten by the database owner raised
AuditChainBroken, AuditFingerprintMismatch and AuditHospitalCheckFailed (naming the hospital) by
mail within two minutes; a switched-off trigger raised AuditTriggerDisabled within a minute; the
watch used 130 MiB. A nightly finding stands until the next nightly re-checks it, also after the
trail was put back. **Owner decisions pending (Q-K32):** the thresholds, the extra memory, and
whether the alert mail may name the hospital.

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

## Loading SNOMED-sized terminology (P1-T10a2, ADR-016)

`up` loads only the small bundled content (HL7, UCUM). A licensed release (P1-T10b) is loaded by
hand, once per release:

1. **Check room first.** A SNOMED International edition plus an extension took about 21 minutes
   in the benchmark. It wrote about 3 GB of WAL and about 3 GB of temp files, and the data
   directory grew by about 1.7 GB at its peak. One loaded version takes about 1 GB. Have at least
   8 GB free on the database volume, and make sure the backups (P1-T8c) are current.
2. **Choose an off-hours window.** The load is one transaction. Hospitals keep answering from the
   active version until it commits, but the database is busy for the whole load.
3. **Run it** with the release's zip files, their sha256 sums and the licence note. The loader
   command is `ihr_terminology_load --rf2 <edition.zip> --rf2 <extension.zip> --sha256 <sum>
   --sha256 <sum> --licence "<licence and affiliate number>" --operator <name>`. It prints the
   version, a summary and the loader's peak memory. The zips must be visible inside the
   container: P1-T10b adds `scripts/staging.sh terminology-load`, which mounts them read-only
   from outside the repo, limits memory and checks free disk first. Until then, there is no
   supported way to load a licensed release on staging.
4. **Check** with `--status`. The load ends with `ANALYZE`; searches are slow without it. If a
   load was interrupted, run `ANALYZE ihr_terminology_concept, ihr_terminology_designation` by
   hand.
5. **Prune** the retired version later with `--prune`, also off-hours.

## Moving to split roles (P1-T19b)

The database `ihr` has two roles (TENANCY_DESIGN §6.10; design: `docs/reviews/p1-t19/design.md`):

- **`ihr_owner`** owns the database and everything in it: the `migrate` one-shot, `admin`
  one-offs (`staging.sh manage`, the terminology loader, `ihr_create_tenant`) and the
  provisioner (it makes and migrates hospital schemas). Address in `secrets/db-owner.env`,
  mounted into those three only.
- **`ihr_app`** serves every request and runs every other worker (backend, celery-worker,
  celery-beat, staff-worker): data only, with no right to change a schema, an audit trigger, a
  grant or the terminology tables. Address in `secrets/app.env`.
- `ihr_audit_watch` (the audit watch, P1-T21) and `ihr_dump` (the nightly dumper, P1-T22a:
  CONNECT and `pg_read_all_data` only; address in `secrets/dumper-db.env`) log in for their
  services only.

Every app container runs with `IHR_DB_ROLES=required` (the deployment default; the settings refuse
anything else on a server), declares its role (`IHR_DB_ROLE`), and runs `manage.py
ihr_db_role_check --for <its process>` before its process starts: a container given the wrong
address or role exits with `ihr-db-role: refused role=<app|owner> reason=<code>` and
restart-loops (DatabaseRoleRefused). `migrate` checks the roles themselves first (`--for migrate`:
attributes, memberships, stored settings, the database's grants, large objects), and its
post_migrate converges the app role's grants (`ensure_grants`, as the owner).

| Command | What it does |
|---|---|
| `scripts/staging.sh db-split-secrets` | **On the deploying machine** (as `s3-init`; `IHR_STAGING_OUT=<dir>` for another folder): a **new** owner password (never the old shared one) into `secrets/db-owner.env`, an app password into `secrets/app.env`'s `DATABASE_URL`, and the old `IHR_DB_PASSWORD` out of `secrets/db.env`. Prints file names only. Idempotent: a secret already made is kept, never replaced (after an interruption, run it again); anything it does not recognise is refused untouched. **Copy the owner password into your password manager** (it is in `secrets/db-owner.env`) |
| `scripts/deploy.sh --host … --operator <you> --release` | The first deploy after the split: refused without `--release` (and refused if the deploying machine has no `db-owner.env`). `staging.sh release` sees the database still has its single role and moves it inside the window: hold every hospital, stop the workers, drain, seal (the outgoing build with its own address), restore point, **then stop every app container, rename `ihr` to `ihr_owner`, set both passwords, make `ihr_app` (and the watch and dump roles), reset every stored setting (in every database), revoke the large-object creators from PUBLIC** (one transaction, as postgres), then `ihr_db_role_check --for migrate`, the migrations (converging every schema's grants as the owner), `up`, smoke |
| `scripts/staging.sh db-roles --migrate [--operator <you>]` | The same move without a release, **only on a stopped stack whose backend image is the build of the release logged last**: after a restore of a moment before the move. Refused (nothing changed) while any app container runs, or when `docker compose ps` cannot say whether one does (on a running server the roles move inside `release`), while a release is unfinished, with no release logged, or when this stack's backend image is not the last logged release's (it would migrate a build that was never released, outside the window, with no restore point). Starts only the database, moves, converges the grants (`ihr_db_role_check --for migrate`, `migrate_schemas --shared`, a fleet-wide `ihr_fleet_run migrate`, which also lifts the restored holds of every hospital it finishes), `up`. A move that stops part-way says where and how to finish. Then `scripts/staging.sh smoke`, and `fleet window --clear` if the restored database brought a window back |
| `scripts/staging.sh db-roles --serve-previous [--operator <you>]` | A release that stopped **before** the move (its backup, restore point or the stop failed): the database still has its single role, so `up`, every rollback and the owner's one-offs refuse or cannot log in. This starts the existing containers of the build that ran again as they were (`docker start`, as `start-existing`: the backend first, its other containers after the holds are lifted; never `up`, which would recreate them with the new images and secrets, nor `docker compose start`, which fails on the `migrate` dependency), lifts the holds and clears the window with that build (in its backend, as `ihr`). The release stays unfinished: fix the cause and run `release` again. The release's own failure message names it; before the move, `fleet release`, `fleet hold` and `fleet window` also run in the running backend, so while its backend still runs the message also names `fleet release` then `fleet window --clear` (they leave stopped workers stopped: `start-existing` them) |
| `scripts/staging.sh db-roles --assert` | As postgres, both roles again as the model has them: passwords from the secrets, every stored setting reset in every database (`RESET ALL`, and `IN DATABASE <each> RESET ALL` for every row `pg_db_role_setting` holds; it fails if any is left), no memberships, the large-object revoke. **Every `up` does this**; this is the runbook of DatabaseRoleLoginFailed and of a `role_settings` refusal. Then `scripts/staging.sh up` |
| `scripts/staging.sh db-roles --status` | Whether the secrets and the database are split |

**How the passwords reach Postgres:** `staging.sh` makes a SCRAM-SHA-256 verifier from each
password and sends `ALTER ROLE … PASSWORD '<verifier>'` to `psql` as postgres on stdin: the
password itself never reaches Postgres, its logs, `pg_stat_activity` or a command line, and
`db.env` holds neither. A new server's first start (`initdb/10-roles.sh`) makes `ihr_owner` with
no password; `up` sets it before anything connects.

**A plain `up` never moves:** on a database that still has its single role it refuses and names
the move. `up` with secrets that are not split refuses too (`db-split-secrets`).

**Going back:** a rollback to a release from before the split is refused before anything changes
(its kept stack would start the app as `ihr`, which no longer exists): fix forward. The same holds
for a release from before the split object-storage keys (P1-T20b; "Releases"). So is any
rollback while the database still has its single role (a release that stopped before the move):
its `up` would refuse that database after the image tags moved. Then `db-roles --serve-previous`
serves the build that ran, and a release's failure message names it instead of a rollback. The move's
release takes its restore point before the move, so the database as it was is in the backups;
going back to it by hand means restoring that point and the old secrets (kept only if you saved
them) and the old stack: not scripted.

**Backups and restores:** pgBackRest restores the whole cluster (physical), roles, passwords and
grants included. A restore of a moment after the move needs nothing more (`up` re-asserts the
passwords from the secrets). A restore of a moment before it brings back the single role: `up`
then refuses, and `scripts/staging.sh db-roles --migrate` moves it again (on the stopped stack).
The restore drill moves the roles on its copy when the copy predates the move, gives them the
secrets' passwords and checks the audit trail as `ihr_owner`.

**Proof** (local staging proof on this machine, 13-11-2026, Postgres 18.6; 3 hospitals, a few
hundred audit entries each, so these are lower bounds):

| Step | Result | Time |
|---|---|---|
| Previous release (one role `ihr`, `IHR_DB_ROLES` off) made, logged; `release` / `up` with unsplit secrets | both refused, nothing changed | 0 s |
| `db-split-secrets`, twice | three files written (600), file names only printed; the second run changed nothing | < 1 s |
| Plain `up` on the single-role database | refused before the database container was touched | ~5 s |
| `release --now` with the move | hold, seal (old build, its own address), restore point, every app container stopped, the move (one transaction: 1 s), `--for migrate` check, grants converged (public 25, each hospital 213), `up`, smoke 0 FAIL, logged. The first try found a bug (the large-object revoke ran in the wrong database; `--for migrate` refused, as designed); fixed, then the release run again kept the window's restore point and finished | 241 s (`up` 146 s; grants 14 s) |
| The app role tries: disable the audit triggers, change or delete or truncate the trail, forge a seal, create a table or schema, drop another hospital's schema, a temporary table, write terminology, write a hospital row, fake a migration, a large object, `SET ROLE ihr_owner` | every one refused by Postgres | — |
| The app role stores a setting on itself | its next process refuses (`role_settings`); `db-roles --assert` (1 s) clears it | 1 s |
| Review fix (S1/C3), on a throwaway Postgres 18.6 with `staging.sh`'s own `db_roles_apply`: `ihr` stores defaults for itself in `postgres` and `template1`, then the move; `ihr_app` stores them in `postgres`, `template1`, `keycloak`, `ihr` and role-wide, `ihr_owner` in `postgres` | the move left 0 rows; the run-time check saw all five of `ihr_app`'s (`every database, ihr, keycloak, postgres, template1`); the old reset (role-wide and `IN DATABASE ihr`) would have left 4; `db_roles_apply` (every `up`, `db-roles --assert`) left 0, and again 0 | < 1 s |
| Web app given the owner's address; celery declaring the owner; `IHR_DB_ROLES=off`; web app declaring the owner | each exits non-zero before its process starts (`refused role=app reason=wrong_role`, `role=owner reason=process`, two settings refusals) | 2-4 s |
| `ensure_grants` over public and 3 hospitals, nothing to change | 0.04 s (the app's first-connection check 0.0-0.1 s) | — |
| `restore-drill` (newest; and to a moment before the move) | newest passed as `ihr_owner`; the earlier copy's roles were moved in the copy and its chains read, failing only on the documented first-hour fingerprint rule | 13 s each |
| `rollback` to the release before the split | refused, nothing changed | 0 s |
| Live database restored to the pre-move point, `up` refused, then `db-roles --migrate` | moved (1 s), grants converged (3 s fleet-wide), `up`; holds lifted with `fleet release` / `fleet window --clear` (as the owner); smoke 0 FAIL | 120 s |
| A hospital made after the move (owner), written by the web app (app) | grants converged at its migration; write and audit entry fine; report clean | 78 s |

## Postgres operating rules and the back-channel service (P1-T23)

**Process limits.** Every Django process opens its database sessions with three limits as startup
options, from one table (`plugins/ihr_platform/ihr_platform/db_limits.py`, chosen by `IHR_DB_LIMITS`
in compose, else derived from the process; a plain `compose exec backend python manage.py ...` on staging inherits the
backend's `web`, since compose sets it on every service):

| Profile | Processes | Statement | Lock | Idle in a transaction |
|---|---|---|---|---|
| `web` | backend, backchannel, celery-beat, dev runserver | 30 s | 10 s | 60 s |
| `worker` | celery-worker, staff-worker, and an app-role one-off that asks for it (`-e IHR_DB_LIMITS=worker`, as the release's audit check does) | 300 s | 30 s | 300 s |
| `owner` | migrate, admin, provisioner, restore, fleet runner, loaders | none | none (DDL paths set their own `SET LOCAL lock_timeout`) | 30 min |
| `watch` | audit-watch | 300 s | 10 s | 60 s |
| `test` | the test runner | 30 s | 10 s | 60 s |

The audit export's snapshot sets its own 10 minutes idle. They are not a security control (a session
may `SET` them again); they stop one stuck request or forgotten shell from holding locks for ever.
The role check reads the live values back at the first connection and at every later one (reason
`limits`, alert DatabaseRoleRefused), so a pooler that drops startup options is found. A cut-off
shows as Postgres's `canceling statement due to statement timeout` (or `lock timeout`) or `terminating connection due to
idle-in-transaction timeout`; more than five in ten minutes fires DatabaseStatementTimeouts: find what
holds locks or sits idle (`pg_stat_activity`) before raising any limit.

**Server settings** live in `deploy/postgres/server-settings.txt` and are carried by the staging
`db` command, the dev override, CI's override and both recovery starts of `staging.sh`
(`max_locks_per_transaction=256`: a restore holds 3,794 locks, and a copy that replays this server's WAL
refuses to start with a smaller value; `effective_cache_size=3GB`). `/dev/shm` is 256 MB
(`IHR_PG_SHM`; Docker's 64 MB failed at about 158 hospitals; measured need at the cap of 100 is under 64 MB). `/dev/shm` and
`shared_buffers` are both charged to the db container's memory limit, so they plus about 512 MB for connections must fit in it
(`scripts/test_staging_postgres.py`): a larger `IHR_PG_SHM` needs a larger `IHR_PG_MEMORY`. Until the 8 GB test, `shared_buffers` is
512MB and the db limit 1536m; the 8 GB server's values are `IHR_PG_SHARED_BUFFERS=768MB` and
`IHR_PG_MEMORY=2304m` in `.env` (about 80 MB per connection at 100 hospitals, P1-T1c).
**Memory limits add up to more than the host (review B2).** With the 8 GB values the `mem_limit`s of the core services
sum to about 7.1 GB, and monitoring, logs, dashboards, dumps and backup add about 3 GB more: the limits are ceilings, and
only measured use fits in 8 GB. If use did reach the ceilings the kernel's OOM killer would choose among all processes, so
the database (`oom_score_adj: -500`) and Keycloak (`-300`) are scored below the sidecars. The 8 GB load test records the real
total (`docker stats`, host free memory, swap).

`staging.sh up`, `release` and `db-roles --assert` check them (`db_server_check`): the lock table,
the planner setting, `shared_buffers` against `.env`, `/dev/shm`, and no setting stored for the whole
database (`ALTER DATABASE ... SET`; the role check refuses it too). A failure names each difference;
`staging.sh up` recreates the db container with the current settings (a restart is needed for the lock
table and `shared_buffers`).

**The back-channel service.** Keycloak posts logout tokens to `http://backchannel:9000/api/ihr/backchannel-logout/`
(its own small gunicorn: 1 worker x 2 threads, 256 MB, internal network only, no Traefik route, no client
key; it answers that path, `/ping/` and `/health/` only). Before, the post landed in the same eight threads
as the sign-out that waits for it. **Nothing to run by hand:** `staging.sh up` (and so every `release`) runs `ihr_keycloak_sync --realm --all` as the admin one-off (it holds the Keycloak bootstrap client; a plain `manage` one-off does not, so it cannot run the sync), and each hospital's Keycloak client takes the new address there; until the first `up` of this build Keycloak keeps posting to `backend`, which still answers (as before, in the shared threads). To go back: set `IHR_BACKCHANNEL_LOGOUT_URL` to `backend` in `compose.yaml`, sync, drop the service.

**Load test on an 8 GB server** (owner; blocked until there is one): see `docs/reviews/p1-t23/build.md`
("The 8 GB load test") and `scripts/load-test.py`. Never reported from this machine.

## Releases (P1-T18b)

A **release** replaces the backend, web app, Postgres and Keycloak images. Hospital schemas
change only inside an **announced window** (Q-T1: about 02:00 IST), with every hospital held
(503 "upgrading", and the web app's notice) so no request writes into a schema while it
migrates. Design: `docs/reviews/p1-t18/design.md`.

| Command | What it does |
|---|---|
| `scripts/staging.sh fleet window --start "DD-MM-YYYY HH:MM" --minutes 90 --note "…" --operator <you>` | Announce the window ahead (IST; public: `/api/ihr/bootstrap/` carries it for the web app's notice). Runs in the running backend when one runs (after deploy.sh has loaded a new build too: never the new build on the old schema). E-mailing hospital admins is yours: it is not built |
| `scripts/deploy.sh --host … --operator <you> --release [--now]` | Build, send, then `staging.sh release` on the server, **detached there** (`setsid nohup`: a dropped SSH connection never stops it half-way); deploy.sh follows its output (`deploy/staging/releases/release-<tag>-<time>.out`, its exit status in `….out.rc`) until it ends. A build that changes the hospitals' or the platform's schema (its migration fingerprints differ from the release logged on the server; the first P1-T18 release on a server with a database), and any build while a release is unfinished on the server (`releases/attempt`), is **refused without `--release`, before anything is sent**. A build without migrations deploys with a plain start as before |
| `scripts/staging.sh release [--now [--minutes N] [--note T]] [--operator <you>]` | On the server, with the new images already loaded (deploy.sh did it), the old ones still running. Refused if the backend is not running while another app service (a worker, the web app) is: stop the stack first (`down`) or start the old backend again (`scripts/staging.sh start-existing backend`); refused too if `docker compose ps` cannot say which run. First it writes the release's record (`releases/attempt`), before any change. In order: **(1)** refuses unless an announced window is open now (`--now`: announce one from this minute); **(2a)** the full audit check (`ihr_audit_verify --all`: every hash chain and stored fingerprint) by the outgoing build while it still serves, capped (`IHR_RELEASE_AUDIT_SECONDS`, 1800 s; its result is the release's `audit=`); **(2)** holds every hospital in one transaction (`ihr_fleet_run hold`, by the outgoing build); **(3)** stops `celery-worker`, `celery-beat`, `provisioner`, `staff-worker` (not paged as down until the window's end, below); **(4)** waits until no transaction that began before the hold is open (60 s cap, `IHR_RELEASE_DRAIN_SECONDS`); **(5)** seals the audit trail and writes one fingerprint, with the outgoing build and the worker's add-only key: no chain recomputed, no fingerprint read (capped: `IHR_RELEASE_SEAL_SECONDS`, 300 s; `seal=` and `seal_s=` in the log; skipped, and said, without the fingerprint bucket); **(6)** `pg_create_restore_point('release-<tag>-<epoch>')`, a name never used before, its LSN and time recorded (a full backup first if the repository has none); **(7)** the platform's migrations, then `ihr_fleet_run migrate check roles root packs` (2 hospitals at a time, in the logged `migrate` container); **(8)** `up` with the new images; **(9)** smoke, which checks every active hospital. Logged in `releases/releases.log` only when every live hospital is served and smoke passes; then the window is cleared. The same build deployed again (a plain start for a changed setting) is logged once |
| `scripts/staging.sh release --finish` | After a failed hospital was fixed and resumed: checks again (every hospital served, smoke), logs the release, clears the window |
| `scripts/staging.sh rollback [--operator <you>]` | Back to the release before the running one, or, while a release is unfinished, back to the release logged last (whether the unfinished one had started or still stopped before its start), **only when nothing changed in the database or Keycloak**: the hospitals' and the platform's fingerprints equal, and the same Keycloak and Postgres images; or the unfinished release stopped before its restore point (nothing migrated). Checks first that every image of the target is still here (nothing changes otherwise), and refuses while hospitals are held for a window with no release in progress (nothing says what held them). Then it re-points the backend and web app images, restores that release's stack files (server), runs `up`, lifts the window's holds, syncs the roles again (holding any hospital whose sync this build refuses), clears the window and runs smoke; the rollback is logged after smoke (a rollback that stopped is run again as it was). Otherwise it refuses and says why |
| `scripts/staging.sh rollback --restore [--operator <you>]` | Back across migrations. Before anything changes: every image of the target is here, a backup from before the restore point exists, the repository holds the WAL up to the point (else, with no room for a copy, refused), and the disk has room for a copy of the whole database volume (`pg_wal` included) plus the 20 GB floor (`IHR_DEPLOY_MIN_FREE_GB`). Asks you to type the target release's tag, stops the stack (`down`, **never `down -v`**: that also deletes the log store, the signing keys and the WAL spool), keeps the current database in a new volume `indiehr-staging_pgdata-pre-rollback-<tag>-<time>` when the disk has room (marked `.ihr-rollback-copy-complete` when whole; a copy that fails is removed and nothing is restored; else the database is replaced: the prompt says which), points Postgres at the target's image, restores with pgBackRest to the window's restore point (`restore --target-name release-<tag>-<epoch> --set <the backup before it>`), logs the rollback, starts the previous release's images, serves the hospitals again, runs smoke and then a full backup of the new timeline |
| `scripts/staging.sh releases` | The releases in force, newest last (and one in progress) |

**Staff sign-in resets during a hold (P1-T24b, Q-K121).** Before an admin's "Reset sign-in" is
carried out, the `staff-worker` reads every kept hospital's schema to make sure the person is a
hospital-wide admin nowhere (`invitations._admin_anywhere`). It fails closed: while **any one**
hospital is held (this window, a hospital left held by a failed migration, a single-hospital
restore, a db_roles gate), every reset at every hospital ends with the card's "ask again later"
(`try-later`; logged `staff request <id> refused: try-later`, and as one line
`ihr-staff: hospital=<slug> request=<id> action=reset_sign_in outcome=try-later`). `try-later` is final
for that request (not re-queued; review A5). Nothing to repair: once every hospital is served again, the
admin asks again. Resends and cancels are not affected. **Alerts** (`deploy/logs/alerts/staff.yml`,
review K5): `StaffResetRefused` fires when a hospital sees 3 or more `protected`, `not-allowed`,
`admin-target` or `reset-refused` lines in an hour (an admin probing the protected path, or a facility
admin trying a reset); look at that hospital's audit trail (the POSTs are recorded) and ask the admin.

**What is kept** (`deploy/staging/releases/`, git-ignored, mode 600): `releases.log` (per release:
tag, time, every image id, both migration fingerprints, the restore point and its LSN and time,
the audit check's and the seal's results; per rollback: from, to, restored or not; never a
setting or a secret, `scripts/release_log.py` refuses any other value); `attempt` (the release in
progress, written before its first change: a release whose smoke never passed is never a
rollback target, but its restore point is used. The restore point belongs to the open window,
not to a build: a `release` of a fixed build while one is unfinished keeps that point, `rp_from`
naming the release it was taken for; a plain start refuses then);
`stack-<tag>.tgz` (the tracked stack files exactly as deploy.sh sent them: never `.env`,
`secrets/`, `generated/` or keys). Every image is tagged `:<tag>` as well as `:staging` (checked
against its id): `<tag>` is the commit's first 12 hex digits, `<sha12>-dirty-<8 of the image id>`
for a build with uncommitted changes. The images of the last 3 logged releases (and the running
one) are never removed; deploy.sh checks for the 20 GB floor plus the images' size before it
loads any (`IHR_DEPLOY_MIN_FREE_GB`).

**A hospital fails in the window.** It stays held (503 "upgrading") while the others are
served; `release` exits non-zero and leaves the window announced. Its newest `upgrade`
TenantOperation (console, `?kind=upgrade`) names the step and a value-free error; the
`HospitalNotServed` alert names it, and keeps firing while it stays down (the worker names a
window-held hospital every minute once its step in the window failed). Fix the cause, then
`scripts/staging.sh fleet --resume <the fleet run> --operator <you>`, then
`scripts/staging.sh release --finish`; or fix it in a new build and release that (it keeps the
window's restore point). While a window's hold outlasts its budget (the announced window's end;
120 minutes after the hold with no window), the worker writes
`ihr-fleet: hospital=<slug> outcome=overrun` every minute and **ReleaseWindowOverrun**
(`deploy/logs/alerts/fleet.yml`, critical) fires.

**If the release stops part-way**, it says what it changed:
- *before the hold* ("did not start"): nothing was held, stopped or migrated; the record of a
  release in progress is as it was. A window announced by `--now` stays (clear it with
  `fleet window --clear`, or release again inside it);
- *held, nothing migrated* (before its restore point): every hospital is held and the workers are
  stopped; the database is unchanged. `scripts/staging.sh rollback` (no `--restore`) serves them
  again with the running build and clears the window; or fix and `release` again;
- *after its restore point* (the platform's migrations fail, a pack is bad, before or after the
  start): run `release` again after the fix (any build: the window's restore point is kept) or
  roll back (`rollback` leaves the unfinished release for the release logged last, even when
  that one still runs; `--restore` goes to the window's point). Nobody writes the overrun line
  while the workers are stopped, so stay at the console.

The workers a window stops do not page **ContainerNotRunning** until the window's end: the
release writes `ihr_release_workers_stopped_until_seconds` (`<textfile dir>/ihr_release.prom`)
and removes it when it ends, so a release that stops with the workers down is paged 5 minutes
later. A rollback that stops says where (images re-pointed; the stack down with the database
unchanged; during the restore; restored and logged) and how to go on.

**Rollback to a release from before P1-T20b: refused, nothing changed.** Once this site's
object-storage keys are split (`s3-split-keys`), a release whose kept stack predates them (its
`deploy/s3/start.sh` knows only the combined `ihr-care` key) is refused by `rollback`, with or
without `--restore`, and never named in a release's advice: its backend would sign patient files
with the pictures key, its gateway would refuse both new keys (and exit on its next start), and
its set-up would make the pictures bucket public again (Q-T3). Fix forward. The first release
onto P1-T20b therefore has no rollback; if it stops, fix the cause and run `release` again.
If it stops **before any migration** (every hospital held, the workers stopped, the database
unchanged), the backend that ran still runs with the keys it was started with, and the release
recreated no container (its repository read with the backup service down recreates nothing): its
advice then names `fleet release --operator <you>` and `fleet window --clear --operator <you>`,
which run in that outgoing build (while the release's migrations have not started, `fleet
release` and `hold` go to it, never to the new build on the old schema), then `start-existing`
for the stopped workers. It names them only while the running gateway still has that backend's
key (a provider site: while the old `BUCKET_KEY` still works there), and this holds only until
the gateway restarts (the deployed `start.sh` refuses the combined key).

**What each way back costs:**
- **Fix forward:** nothing is lost; the failed hospital waits.
- **Rollback without a restore:** nothing is lost; a few minutes of `up` (measured below).
- **Rollback with a restore:** every hospital is offline for the whole restore, and everything
  written after the restore point is lost: hospitals served by the release being left (and
  their staff's Keycloak changes: invitations, password and authenticator resets, disabled
  accounts) lose those writes; inside the window, before any hospital was served again,
  nothing is lost. Audit fingerprints stored after the point will not match the restored
  trail (expected: record it in the incident). It ends with a full backup of the new timeline
  (`backup-now full`; run it by hand if it failed). Use it inside the window; outside it,
  prefer fixing forward. ADR-007's 4-hour restore target is the upper bound.

**Not covered:** the first release that brings P1-T18 itself onto a server whose running build
has no gate (`ihr_fleet_run hold` does not exist there): stop the stack first
(`scripts/staging.sh down`), then `release` (with nothing serving it skips the announcement,
hold, drain and seal; it refuses while any app service still runs). The changes from the
P1-T18b review (the record before the first change, carried restore points, unique point names,
the detached release, the kept copy's sizing, the seal-only window) are proven by
`scripts/test_release_scripts.py` and the rule tests; the drill below predates them and is to be
run again through `scripts/deploy.sh --release` against the local VM. Unpacking an older stack archive leaves files a newer release added
(harmless: compose reads only the files it names). The nightly backup (02:30) may run inside
the window; it does not block the restore point.

**Drill** (local staging proof on this machine, 13-11-2026; 3 hospitals, a few hundred MB of
database, so these are lower bounds; a server's restore scales with the database and WAL):

| Step | Result | Time |
|---|---|---|
| Release N (plain start: `release-note`, `up`, smoke, `release-record`) | logged; every hospital served | 94 s |
| Release N+1 with a tenant migration, `beta` forced to fail | first try stopped at the restore point (a bug, fixed): every hospital stayed held (503), window kept; run again: alpha and gamma migrated and served, beta failed (value-free error naming the migration), stays 503, `ihr-fleet: hospital=beta outcome=failed`, release exit 1, window kept, nothing logged | 30 s + 149 s |
| Resume (cause removed; `fleet --resume`, then `release --finish`) | beta migrated; all served; release logged; window cleared | 4 s + 49 s |
| Release N+2 (no migration) | all held for its 40 s of hold, seal, restore point and migrations; logged | 168 s |
| Rollback N+2 to N+1 (no restore) | backend and web app re-pointed, `up`, smoke; nothing lost | 126 s |
| Rollback N+1 to N without a restore | refused at once: the hospitals' schema changed | 0 s |
| Rollback N+1 to N `--restore` | a wrong typed tag refused with nothing changed; then stop, copy kept (`pgdata-pre-rollback-…`), restore to `release-<N+1>` from the full backup before it, N's images, `up`, holds released, smoke 0 failures; a profile edit made after N+1 was gone (as stated), beta's migration row gone; every other volume (logs, keys, WAL spool, backups state) untouched | 205 s (stack down about 35 s of it, then `up`) |
| `backup-now full` after the restore | the new timeline backed up | 20 s |
| Window budget | a window of 1 minute and a held hospital: the worker wrote `ihr-fleet: hospital=gamma outcome=overrun` about 90 s later (the alert path itself is proven by `scripts/check-logs.py`; a proof logs to Docker's files, not the journal) | — |

## Roles and rollbacks (P1-T11a, ADR-017)

At every start, the `migrate` one-shot syncs CARE's roles and permissions, and ours, into every
active or suspended hospital (`ihr_fleet_run ... roles`). It runs `sync_permissions_roles
--check` for all of them first, then the sync itself for those that passed. The sync is our
guarded version (`ihr_roles`). It **refuses** rather than delete (P1-T18a: a refusing hospital
is held, answers 503 "upgrading" and is alerted on, `HospitalNotServed`; the others start):

- a system role that someone still holds;
- a permission that a hospital's own role still has.

CARE's own sync would delete both silently, along with the memberships or grants that point at
them.

**If a hospital is held with "role sync would be refused":** the image you started is missing a role or
permission that hospitals use. Usually that means a rollback. Roll forward to the image that
registered it. If the role is really to go, reassign its holders first. Role names are
permanent: a retired role stays registered with no grants. Then run
`scripts/staging.sh fleet --resume <the start-up's fleet run> --operator <you>` (or simply `up`
again): once its roles pass, the hold the refusal set is released.

**Never roll back past P1-T11a** once hospitals hold any of our roles. Older images run CARE's
unguarded sync, which would delete those memberships in every hospital.

## Hospital root (P1-T25, Q-K76 B)

Each hospital has one **root**: a top-level CARE organisation of type `role` named "Hospital
administration", marked `metadata.ihr = "hospital-root"` (`plugins/ihr_platform/ihr_identity/roots.py`).
Only an admin role on it (Administrator, Admin or Facility Admin there) opens the hospital's audit
trail, reference pack decisions, records-grant ends and sign-in resets. An admin role on a state, a
district or any other top-level organisation does not (Q-K120 superseded). With no valid root (none
marked, more than one, or the marked one inactive, deleted, under a parent, of another type or with a
managing organisation) **nobody** has those screens: the hospital is still served. CARE itself refuses
non-superusers who try to edit, deactivate, retype or delete a role organisation or add a child to it;
`ihr_identity.tests.test_root` pins those rules at every upstream sync. The person protected from a
sign-in reset keeps the wider rule (an admin role on any top-level organisation, Q-K130 A).

- **New hospitals:** provisioning's `first_admin` step makes the root and makes the first admin its
  Administrator (no state membership any more, Q-K128 A); `activate` refuses a hospital whose root
  has no admin. `ihr_create_tenant` makes a root with nobody on it.
- **Existing hospitals:** the fleet step `root` (in the start-up one-shot `check roles root packs` and
  in every release) makes a missing root and, when the root has no admin, places the provisioning
  record's first admin **only if** that person still exists, is active or awaiting setup, and is an
  admin on some top-level organisation today; their own Administrator row on the state is then
  soft-deleted (audited). Nobody else is moved. A single-hospital restore runs the same step after its
  migrations, so a dump from before P1-T25 comes back with a root.
- **A provisioning operation in flight across the upgrade** (past `first_admin` before this build, not
  yet live, for example failed later at Keycloak): the fleet step skips hospitals still being
  provisioned, and the runner never re-runs a succeeded `first_admin`. Resume the operation: `activate`
  makes the root and places the operation's own first admin there (and drops their state row) before it
  checks, so the hospital goes live with them as its admin (review T1).
- **An old unmarked "Hospital administration" organisation** (made by the earlier dev seed on some dev
  hospitals) is never adopted by name: it stays beside the marked root as an ordinary top-level role
  organisation, and `hospital-root show` lists it under "other top-level organisations". It opens
  nothing any more; remove it with a superuser's care when nobody needs it.
- **Log line** (`ihr_identity.roots`): `ihr-root: hospital=<slug> action=<ensure|place|remove-state>
  outcome=<done|exists|no-admin|invalid|failed|state-admins>[ n=<k>] by=<operator>`. `failed` is the
  fleet step raising or deferring (the root was not made or not checked; review T3). `state-admins n=<k>`
  counts admin rows still on government organisations (they list every patient with an address in that
  state): remove them with `remove-state` once the hospital confirms. The fleet step, a restore and
  provisioning write their lines from logged containers (`migrate`, `celery`). **`hospital-root place`
  and `remove-state` run in the admin one-off, which is not logged**: an operator's own write is
  recorded in the hospital's audit trail (`root:<operator>`, with the reason) and, since P1-T27a, the
  session's start and end lines reach the log store (`ihr-operator:`, "Operator access record"). An operator name that an automatic source writes (`fleet-*`, `restore-*`,
  `provisioning-*`, `demo-seed`, `ihr_create_tenant`, `test`, `startup`) is refused, so a manual write
  cannot pose as one (review T4).
- **HospitalRootProblem** (`deploy/logs/alerts/root.yml`, warning): a `no-admin`, `invalid` or `failed`
  line in 24 hours from the start-up, a fleet run or a restore. The hospital is served; its admin
  screens are closed. **Known limit (review A2):** the root is checked only at start-up, a release and a
  restore, so one made invalid while the hospital runs (only a superuser can do that, Q-K131) stays
  silent until the next of those; `hospital-root show` checks it at any time.

| Command | What it does |
|---|---|
| `scripts/staging.sh hospital-root --operator <you> --reason "<ticket>" --slug S show` | The root, its admins, and every admin role on other top-level organisations (who lost the admin screens with P1-T25). Usernames only. |
| `scripts/staging.sh hospital-root --slug S place <username> --operator <you> --reason "<ticket>"` | The recovery when nobody is hospital-wide (Q-K122): makes that person Administrator on the root. Check first who the hospital's admin is (in person or a known phone number). Refuses superusers and service accounts. |
| `scripts/staging.sh hospital-root --slug S remove-state <username> --operator <you> --reason "<ticket>"` | Soft-deletes that person's Administrator rows on top-level government organisations (review S5). |

An **invalid** root is never replaced automatically and never "fixed" by marking a second
organisation: look at what changed (the audit trail records every write to `emr_organization`), then
put the organisation right with a superuser's care, or ask for a decision. A root admin can remove
every other root admin, themselves included (CARE's member removal): `place` is the way back.

## Operator access record (P1-T27)

Design: `docs/reviews/p1-t27/design.md`, and binding where they differ its attack's "Orchestrator
decision" (`docs/reviews/p1-t27/attack.md`; Q-K134 to Q-K139). ADR-010's P1-T27 amendment says what it
proves and what it does not. P1-T27a (this part): the session lines, the grammar, the Postgres session
lines and the alerts. P1-T27b: one `operator` entry per hospital in the hospital's own audit trail.
P1-T27c: the host's kernel audit records.

### How to run commands now

Every `scripts/staging.sh` run that reaches a privileged call is an **operator session**: an admin,
migrate or audit-supersede one-off, an exec in an app, worker, database, backup, dumper or archive
container, or psql/pg_dump as `postgres`. Give your own name, always:

```
scripts/staging.sh <command> [--operator NAME] [--reason "TEXT"] [--hospital SLUG|all] <its arguments>
export IHR_OPERATOR=dhruv            # the same as --operator on every command of this shell
```

- The three options come **right after the command** and are not passed on (`manage --operator dhruv
  --reason "INC-12 bed 4 fix" --hospital alpha shell`). A command's own `--operator` (release,
  rollback, restore, db-roles, hospital-restore, audit-supersede, hospital-root, fleet's) must name the
  same person; two different names are refused.
- **Name:** 2-32 of `a-z 0-9 . -`, starting with a letter; never `ihr`, `root`, `admin`, `postgres` or
  the bootstrap's admin user (`/etc/indiehr/admin-user`). There is no default: the deploy account is
  shared, so its login name says nothing (Q-K134). `scripts/deploy.sh --operator NAME` (or
  `IHR_OPERATOR`) passes it to every call on the server.
- **Data tier** (they reach patient data or raw logs): `manage` (`shell` included; SQL on the live
  database is `manage ... shell -c "..."` as `ihr_owner`; `dbshell` fails: the admin image has no `psql`),
  `hospital-root`, `dumps-get`, `logs`, `logs-archive-get`, `restore-drill --keep`,
  `restore-drill-psql`, `audit-supersede export`. They need `--reason` and a hospital, and open their session before anything runs.
  - `--reason`: 3-120 characters of letters, digits, space and `. _ : / # -`, no number of 6 or more
    digits. A ticket or incident id and a few words: **never a patient's name or number**.
  - `--hospital SLUG` or `--hospital all` for `manage`; `hospital-root` takes its `--slug` (its own
    `--operator`/`--reason` count too); `dumps-get` names the hospital from the dump's bucket path
    (its uuid); `logs`, `logs-archive-get`, `restore-drill --keep`, `restore-drill-psql` and
    `audit-supersede export` are every hospital (`all`).
- **Job tier:** everything else that reaches a privileged call (`up`, `smoke`, `release`, `rollback`,
  `restore`, `fleet`, `keys`, backups, dumps, the restores and drills...). It opens at its first
  privileged call, reason `job:<command>` unless you give one. `release`, `restore-drill`, `dumps-now`
  and `hospital-restore-drill` open every hospital's schema: their session's scope is `all`.
  `audit-verify --slug S | --all` (the full audit check, read-only, in the audit watch's container,
  else the backend: where the fingerprint reader key is) has scope `S` or `all`; it refuses `--seal`
  and `--anchor` (the beat seals, celery-worker writes fingerprints).
- A command that reaches no privileged call (`ps`, `down`, `init`, `releases`, `monitoring-textfile`,
  `ops-tunnel`, the secret and key tools of the deploying machine, `acme-proof`) writes nothing and
  needs no name.

### What is written

Lines to this server's journal (`logger -t ihr-operator`; Fluent Bit ships them to the log store, 180
days, and the daily locked archive, 400 days), each also on your terminal:

```
ihr-operator: session=<s> event=start tier=<data|job> operator=<name> command=<cmd> args=<shape> hospital=<scope> ses=<n|none> from=<addr> reason="<text>"
ihr-operator: session=<s> event=scope hospital=<scope> hospital_entry=<opened:<slug>|skipped:<slug>:<why>,...|->
ihr-operator: session=<s> event=end outcome=<ok|failed|interrupted> rc=<n> seconds=<n>
ihr-operator: event=refused why=<why> command=<cmd> operator=<name|<v>|none>
ihr-operator: event=late at=<time> session=<s> was=<start|scope|end> ...
```

- `session`: 12 hex characters, new for every run (an exported `IHR_OPERATOR_SESSION` is ignored).
  `ses`: the kernel's login session id (`/proc/self/sessionid`), which P1-T27c's audit records carry
  too. `from`: the SSH client's address (`SSH_CONNECTION`), else `local`; sshd's own "Accepted
  publickey ... from <addr>" line and the time join it to a key.
- `command` and `args`: the command and the names of its options only; **every value is `<v>`**
  (`manage shell -c "..."` is `command=manage:shell args=-c,<v>`). Shell code, SQL, slugs, dates and
  paths never reach the line (Q-K135).
- `scope` line: the hospitals the session opened, from P1-T27b's `manage.py ihr_operator_open` (run in
  the admin one-off at the start of a data-tier session with a hospital, and before the first one-off
  of a job of every hospital). `skipped:all:old-build`: the running image predates that command (a
  rollback to an older build keeps working). `skipped:<slug>:failed`: that hospital's entry could not
  be written (the others were still tried and stay written); the scope line is written, then the
  session is refused (the command exits 3). Any other failure refuses the session.
- A one-off's own row writes are attributed `operator:<name>` with the session and tier; since a
  write may land in any hospital's trail, their context names no hospital and no given reason
  (scope `all`, `none` or `one`, and a fixed text naming the session). The given reason is in that
  hospital's own `operator` entry and in the log store, joined by the session.
- Every one-off and exec the session starts gets `IHR_OPERATOR`, `IHR_OPERATOR_SESSION`,
  `IHR_OPERATOR_SCOPE` (slug, `all` or `none`), `IHR_OPERATOR_REASON` and `IHR_OPERATOR_TIER`;
  psql and pg_dump connect as `PGAPPNAME=op:<session>:psql|pg_dump`, Django one-offs as
  `op:<session>:<process>` once P1-T27b names them.
- **Postgres** logs every authorized session and its end (`log_connections=authorization`,
  `log_disconnections=on`, with `log_min_messages=warning` and `log_destination=stderr` pinned on its
  command line; `deploy/postgres/server-settings.txt`, checked by `db_server_check` at every `up` and
  release). The host timer checks every minute that `postgresql.auto.conf` holds no setting
  (`PostgresSettingOverridden`, critical). Expect two lines per web request and per Celery task: the
  local proof measures them per hour at two hospitals and extrapolates to 200 against the log store's
  budget (`logs-volume`); a budget overrun is a decision, never a silent drop.
- **Service sessions are named at login.** Postgres writes the "connection authorized" line before
  a client can `SET application_name`, so a service must name itself in the start-up packet:
  the backup service's schedule exports `PGAPPNAME=ihr-backup` inside its own process only
  (pgBackRest 2.60 opens libpq sessions with no name and SETs `pgBackRest [<command>]` later;
  `pg_isready` would say `pg_isready`), and Keycloak's JDBC URL carries
  `?ApplicationName=ihr-keycloak&assumeMinServerVersion=18` (`KC_DB_URL_PROPERTIES`; pgjdbc sends
  the name at login only with `assumeMinServerVersion` 9.0 or more). No container's environment
  sets `PGAPPNAME`: a hand-typed `docker exec <db|backup> psql` stays `psql` and alerts (P1-T27 live
  proof F1-F4; a test keeps it so).
- **A new server's first start alerts once.** On an empty data directory the postgres image's
  entrypoint runs its init scripts (`/docker-entrypoint-initdb.d`) through `psql`, which logs two
  `user=postgres ... application_name=psql` lines; `PrivilegedLoginUnrecorded` fires once for them
  on the first `up` on a new server (and after a `down -v`). Recognise them by the time: the db
  container's first start, seconds after `initdb`'s own lines in its log, before any `ihr-operator`
  start line; note them, no incident. They are not allow-listed (that would let an operator's raw
  `psql` through), and `PGAPPNAME` is not set in the db service (an operator's `docker exec db psql`
  would then carry the same name).

### Fail closed, and break-glass

- No name, a shared or malformed name, a missing or bad reason or hospital: **refused**, nothing run,
  a `refused` line (`OperatorSessionRefused`).
- The journal cannot be written (`logger` fails): **refused**. Break-glass for a job-tier `rollback`,
  `restore` or `hospital-restore` only: `IHR_BREAK_GLASS="INC-9 journal full" scripts/staging.sh
  rollback ...` runs; its lines go to your terminal and to `deploy/staging/operator-pending.log`
  (mode 600), the session's reason becomes `break-glass: <text>`, and the next recorded session ships
  them first as `event=late` lines. Data-tier commands never break glass. The steps a rollback runs
  as nested `staging.sh` calls (`up`, `fleet release|roles|window`, `smoke`, `backup-now`) join the
  rollback's own session (its operator, and break-glass) instead of opening their own: only from a
  running `staging.sh` above them (`op_child`, `op_parent_ok`), never from a file the caller names.
- A local proof (`.local`) or CI writes the lines to stderr only with `IHR_OPERATOR_LOCAL=stderr`
  (`local-proof` sets it unless `.env` has `IHR_LOG_DRIVER=journald`); on a server the setting is
  ignored, and on a host `deploy/host/bootstrap.sh` set up (`/etc/indiehr/installed-at`) it is
  refused whatever else the environment says.
- `logger` is called with `--size 65536` (by default it cuts a message at 1 KiB and still succeeds);
  a line longer than 60,000 characters is refused, never cut. An `all` scope line at 200 hospitals
  is under 15 KB.
- A killed run or a dropped SSH connection leaves a start line with no end line: normal then
  (`OperatorSessionNoEnd` after 12 hours).

### Alerts (`deploy/logs/alerts/operator.yml`)

| Alert | When | What to do |
|---|---|---|
| PrivilegedLoginUnrecorded (critical) | A database session in the last 10 minutes that is neither a reviewed service login (`deploy/postgres/service-logins.txt`: service, role, application name) nor an open operator session's (`op:<session>:...` with a start line in 24 hours and no end line older than the window). | Find who ran it (operator lines, sshd logins). A hand-typed `docker exec db psql` is a mistake to note; unowned, an incident. A new service login is added to the list only after review (the test keeps the rule and the list the same). |
| OperatorSessionRefused (warning) | staging.sh refused a session in the last hour, by `why`. | Usually a typo; repeated `shared-operator` or an unknown person: ask. |
| OperatorSessionNoEnd (warning) | A session started 12-24 hours ago has no end line. | Ask the operator named. |
| KeycloakAdminCredentialChange (critical) | A Keycloak admin event that removes or resets a user's credential by any client other than our onboarding and staff-reset accounts (`ihr-onboarding`, `ihr-staff-worker`). | Whoever resets a sign-in can act as that person, and the hospital's trail would name them: ask who did it in the admin console; unowned, an incident. |
| PostgresSettingOverridden (critical, Prometheus) | `postgresql.auto.conf` holds a setting (not counted: the `restore_command`, `recovery_target*` and `recovery_end_command` lines a pgBackRest restore writes there, which stay after the promotion). | `pg_file_settings` names it; find the session; `ALTER SYSTEM RESET ALL`, restart in a window. |
| JournalSuppressed (critical, Prometheus) | journald dropped lines (rate limit). | Lines of the record may be missing: find the flooding container. |

### What it does and does not prove

- It **attributes and bounds an honest or careless operator**: who ran what, when, from where, for
  which hospitals and why, with the values of the command left out. A hand-typed privileged session
  outside staging.sh alerts.
- It is **attribution by convention, not authentication**: the name is declared; anyone with the
  deploy account can type another person's name. Personal Unix accounts are the answer once a second
  operator joins (ADR-020's production-1).
- Against a **deliberate insider** with the deploy account (the docker group is root on the host) it is
  evidence, not a barrier: application names and start lines can be forged, and the journal, the log
  store and vmalert run on the same host. The off-host copy is the daily locked archive (P1-T9c);
  P1-T27c adds the kernel's audit records. Alert delivery to a second person who is not an operator is
  an owner go-live item (with PR1-4's second channel), not built here.
- It says a session **had access**, not what an operator saw: a shell or SQL session cannot be
  narrowed to rows, and raw SQL inside a session can reach hospitals outside its declared scope.
- Until P1-T27b ships, a one-off's own Django session is named `manage:<command>`, not `op:...`, and
  `PrivilegedLoginUnrecorded` fires for it; the reviewed service-login list is built from the code and
  corrected by the local proof's capture of every authorization line (the live step).
