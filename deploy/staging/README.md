# Staging stack (P1-T8a)

One VM in AWS Mumbai (ap-south-1), Docker Compose, Traefik in front (ADR-003 and its
Amendment 1). Everything runs through `scripts/staging.sh`.

| Command | What it does |
|---|---|
| `scripts/staging.sh init <suffix>` | Writes `.env` (site settings, image digests) and `secrets/*.env` (mode 600, git-ignored). Refuses to overwrite. `<suffix>`: hospitals live at `<slug>.<suffix>` |
| `scripts/staging.sh up` | Database, Redis, migrations, web app, Celery, Keycloak, Traefik; then the Keycloak realms and the two workers' narrow accounts |
| `scripts/staging.sh smoke` | 20 checks through Traefik: what is public and what is not, TLS redirect, HSTS, token issuer, production mode, no query strings in logs |
| `scripts/staging.sh manage <args>` | One-off `manage.py` (never logged; no Keycloak admin credential) |
| `scripts/staging.sh local-proof` | All of the above on this machine (127.0.0.1, self-signed certificate) |

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
production mode, or with CARE's terminology default or Sentry (`settings/secure.py`).

**Not yet** (P1-T8b–d): the VM, DNS and Let's Encrypt certificates (`compose.acme.yaml`), S3
buckets and mail, NTP to NIC/NPL and 180-day journald retention on the host, backups and a
restore drill, Keycloak's own hardening.
