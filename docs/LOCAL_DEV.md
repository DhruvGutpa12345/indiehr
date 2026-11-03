# Running CARE locally (P0-T5)

This setup fits the current **6.7 GB** dev machine. The whole backend uses about 550 MB. The web app is built on GitHub, because a local build needs about 4 GB.

## Start

```bash
scripts/care-dev.sh up          # build image if missing, start db/redis/storage/api/worker
scripts/care-dev.sh seed        # first time only: CARE demo data + our test users (writes deploy/seed/TEST_USERS.md)
scripts/care-dev.sh frontend    # serve the web app bundle on http://localhost:8080
```

Then open **http://localhost:8080** and log in with a user from `deploy/seed/TEST_USERS.md` (git-ignored; created by P0-T5).

> `seed` replaces CARE's public fixture passwords with a generated one. Lost it? `scripts/care-dev.sh reset-passwords` makes a new one.
> Guided tour of the app: [CLICK_THROUGH.md](CLICK_THROUGH.md).

## Stop or inspect

```bash
scripts/care-dev.sh down        # stop everything (data stays in Docker volumes)
scripts/care-dev.sh ps          # status
scripts/care-dev.sh logs backend
scripts/care-dev.sh shell       # bash inside the API container
```

## Get a fresh web app bundle

The bundle has the API URL baked in. To rebuild it on GitHub and download it:

```bash
gh workflow run "Frontend bundle" -f api_url=http://localhost:9000
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
| Bundle built with `deploy/frontend.env` and checked by `scripts/check-bundle-egress.sh`; enforced CSP allows only `localhost:9000` (API) and `localhost:9100` (files) | No third-party calls (P0-T10). Facility map and address search are blocked on purpose |
| Test-user passwords replaced | CARE's fixture passwords are public in its source |

## Ports

| URL | What |
|---|---|
| http://localhost:8080 | Web app |
| http://localhost:9000/api/v1/ | API |
| http://localhost:9000/admin/ | Django admin (superuser `admin`) |
| http://localhost:9001 | File storage console |
| http://localhost:9100 | File storage S3 API (pre-signed file URLs) |
| localhost:5433 / 6380 | Postgres / Redis |
