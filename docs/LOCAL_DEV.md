# Running CARE locally (P0-T5, tenant mode since P1-T2b)

This setup fits the current **6.7 GB** dev machine. It runs in **tenant mode**: one database (`ihr_dev`), one schema per hospital, and two synthetic demo hospitals, each at its own address. The old single-tenant database `care` is left in place but no longer used. The whole backend uses about 550 MB. The web app is built on GitHub, because a local build needs about 4 GB.

## Start

```bash
scripts/care-dev.sh up          # build image if missing, create ihr_dev, start db/redis/storage/api/worker (migrates every hospital)
scripts/care-dev.sh seed        # first time only: hospitals alpha + beta with CARE's demo data and our test users (~3 min each)
scripts/care-dev.sh frontend    # serve the web app bundle; /api is passed through to the backend
```

Then open **http://alpha.localhost:8080** or **http://beta.localhost:8080** (browsers send `*.localhost` to this machine) and log in with a user from `deploy/seed/TEST_USERS.md` (git-ignored).

> Each hospital has its own users and its **own password**: the same username exists in both, as separate accounts. `seed` replaces CARE's public fixture passwords; lost them? `scripts/care-dev.sh reset-passwords` makes new ones.
> `http://localhost:8080` is the platform's own address: it has no hospital, so the app there cannot sign in.
> Guided tour of the app: [CLICK_THROUGH.md](CLICK_THROUGH.md).

## Keycloak (staff sign-in, P1-T3)

Keycloak is optional: it needs about 540 MB more. Staff sign in through it at http://alpha.localhost:8080 and http://beta.localhost:8080 (P1-T4). The backend and the two workers prove who they are to Keycloak with their own keys (P1-T8d2), made and published by `keycloak up`.

```bash
scripts/care-dev.sh keycloak up        # own database "keycloak", start, create/repair realm + both hospitals
scripts/care-dev.sh keycloak seed      # demo staff with fresh passwords + authenticator keys -> TEST_USERS.md
scripts/care-dev.sh keycloak keys status   # the three client keys (rotate <purpose|all>, probe [--negative])
scripts/care-dev.sh keycloak otp dr.meera   # current 6-digit code for a demo user
scripts/care-dev.sh keycloak sync --all --check   # report drift from the baseline (exit 1 if any)
scripts/keycloak-test.sh               # live tests in a throwaway realm (~45 s)
```

- Secrets are in `deploy/dev/keycloak.env` (git-ignored, created on first use).
- Every staff sign-in needs a password **and** a 6-digit code (decision in ADR-008).
- Short of memory? Run `scripts/care-dev.sh keycloak stop` when you are done.
- E-mail: `keycloak up` also starts **Mailpit**, which catches every e-mail Keycloak sends (setup links for new hospitals' first admins, P1-T5c). Inbox: http://127.0.0.1:8025. Nothing reaches a real mailbox.

## Staff sign-in through the backend (P1-T4a)

Staff sign in through Keycloak only; CARE's own password form no longer works on hospital addresses (ADR-009). Until the web app is moved over (P1-T4b), check a sign-in directly:

- http://alpha.localhost:8080/api/ihr/auth/login/?next=/api/ihr/auth/session/ signs you in (password + 6-digit code) and shows who you are at that hospital.
- `POST /api/ihr/auth/logout/` with the header `X-IHR-CSRF: 1` signs out at every hospital.
- The demo staff are linked to CARE users by `keycloak seed` (`dr.meera` is `care-doctor` at both hospitals).
- The web app's "Sign in with your hospital account" needs a bundle built after P1-T4b (`gh workflow run "Frontend bundle" -f api_url=`).
- For a quick idle test, start the backend with a short limit: `IHR_SESSION_IDLE_SECONDS=120 scripts/care-dev.sh up` (warning after about a minute, sign-out at 2 minutes).
- Link another user with `manage.py ihr_link_staff --slug alpha --username care-nurse --sub <Keycloak user id> --yes`.

## Provisioning a new hospital (P1-T5a)

```bash
scripts/care-dev.sh provision-demo gamma          # synthetic hospital live in ~70 s, plus a dev login for its first admin
scripts/care-dev.sh provision status --slug gamma # step table with timings
scripts/care-dev.sh provision start --slug x ... [--stop-after STEP]   # all fields: manage.py ihr_provision start -h
scripts/care-dev.sh provision resume <operation id>
scripts/care-dev.sh provision rollback <operation id>   # only a hospital that never went live
```

Keycloak must be running (`scripts/care-dev.sh keycloak up`). The first admin is created inactive; `provision-demo` gives them a development login (written to `deploy/seed/TEST_USERS.md`) until the real setup e-mail arrives in P1-T5c.

## Operator console (P1-T5b)

- http://console.localhost:9000/ops/ — sign in as `op.owner` (password and authenticator key in `deploy/seed/TEST_USERS.md`; code: `scripts/care-dev.sh keycloak otp op.owner`).
- "New hospital" queues a provisioning run; the `provisioner` container (started by `scripts/care-dev.sh keycloak up`) runs it and the page refreshes itself until it is live (~70 s).
- Operators are on two lists: role `ihr-operator` in realm `indiehr-ops`, and ours: `manage.py ihr_operator add|list|remove`.
- Two workers (P1-T5f): `provisioner` (hospital runs, secret in `deploy/dev/provisioner.env`) and `staff-worker` (invitations, resets, expiry; `deploy/dev/staff-worker.env`). Each file holds only that worker's own narrow Keycloak service-account secret, written by `keycloak up`; neither holds the bootstrap admin credential, and the web app and CARE worker hold none. Logs: `scripts/care-dev.sh keycloak logs`.

## Server kit on a throwaway VM (P1-T8b1)

Proves `deploy/host/bootstrap.sh` and `scripts/deploy.sh` on this machine: the official Ubuntu
26.04 cloud image under KVM (no sudo), its own SSH key, files only in `~/.cache/indiehr/vm/`.
Stop the dev stack first (`scripts/care-dev.sh down`; the VM takes 3.5 GB). The runbook and the
order of commands are in `deploy/host/README.md` ("Prove it locally"). SSH into it:
`scripts/local-vm.sh ssh` (admin) or `scripts/local-vm.sh ssh ihr` (deploy user); remove it with
`scripts/local-vm.sh destroy`.

## Stop or inspect

```bash
scripts/care-dev.sh down        # stop everything (data stays in Docker volumes)
scripts/care-dev.sh ps          # status
scripts/care-dev.sh logs backend
scripts/care-dev.sh shell       # bash inside the API container
```

## Get a fresh web app bundle

The bundle talks to the API on its own origin (`/api`, passed through by nginx with the hospital's address), so one bundle serves every hospital. To rebuild it on GitHub and download it:

```bash
gh workflow run "Frontend bundle" -f api_url=
gh run list --workflow "Frontend bundle" -L1      # wait until completed
rm -rf deploy/dev/frontend-build && gh run download <run-id> -n frontend-bundle -D deploy/dev/frontend-build
scripts/care-dev.sh frontend
```

## What differs from upstream's dev setup

The overrides live in `deploy/dev/care-local.override.yaml`; CARE's files are untouched.

| Change | Why |
|---|---|
| Every port bound to `127.0.0.1` (upstream: all interfaces) | The database (password `postgres`), API and storage were reachable from the LAN |
| `SNOWSTORM_DEPLOYMENT_URL` points to a dead local address | Upstream default sends clinical search text to a third-party IP over HTTP. Diagnosis/drug search will not work until P1-T10 |
| One Celery process (`--pool=solo`), no auto-reload, memory caps | Upstream starts one worker per CPU core |
| Web app served by our non-root nginx image from a CI-built bundle | No local Node build (needs ~4 GB) |
| Bundle built with `deploy/frontend.env` and checked by `scripts/check-bundle-egress.sh`; enforced CSP allows only the page's own origin (API) and `localhost:9100` (files) | No third-party calls (P0-T10). Facility map and address search are blocked on purpose |
| Test-user passwords replaced, one per hospital | CARE's fixture passwords are public in its source |
| Tenant mode: `DJANGO_SETTINGS_MODULE=ihr_platform.settings.local`, database `ihr_dev`; the worker runs `ihr_create_tenant --public --domain localhost` and syncs roles/value sets per hospital (`all_tenants_command --no-public`) | One schema per hospital, as in production (ADR-002) |
| nginx passes `/api/` to `backend:9000` (`API_UPSTREAM`) keeping the browser's host | The backend picks the hospital from the address; no cross-origin API calls |

## Ports

| URL | What |
|---|---|
| http://alpha.localhost:8080 | Web app, Alpha Multispeciality Hospital (synthetic) |
| http://beta.localhost:8080 | Web app, Beta Children's Hospital (synthetic) |
| http://alpha.localhost:9000/api/v1/ | API direct (the host picks the hospital) |
| http://localhost:9100 | File storage S3 API (pre-signed file URLs): SeaweedFS since P1-T8b3 (`deploy/s3/`, no web console). Keys in `deploy/dev/s3-gateway.env` (git-ignored); `scripts/care-dev.sh s3-check` tests every key and lock |
| localhost:5433 / 6380 | Postgres / Redis |
| http://auth.localhost:8081 | Keycloak (staff sign-in), realm `indiehr`; admin console at `/admin/` |
| localhost:9002 | Keycloak health (`/health/ready`) |
