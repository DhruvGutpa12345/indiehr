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

**Done in P1-T8b1:** the host (`deploy/host/bootstrap.sh`: firewall incl. Docker's published
ports, key-only SSH, NTP to NIC/NPL, journald one year, pinned Docker) and deploy over SSH.

**Done in P1-T8b2:** the wildcard certificate through our own acme-dns (above).

**Not yet** (P1-T8b3–d): S3 buckets and mail, backups and a restore drill, Keycloak's own
hardening.
