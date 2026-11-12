# ADR-015: The operator's dashboards and how they are reached

- **Status:** Proposed (auto-accepted by Claude in an unattended run, 2026-11-12; awaiting
  owner confirmation: Q-O13, Q-O14)
- **Date:** 2026-11-12
- **Decided by:** Claude. The evidence: the §5 rubric scorecard (docs/reviews/p1-t9d/scorecard.md),
  a design attack by security and feasibility reviewers (17 findings confirmed, folded in
  below), Grafana 13.3.1, vmauth v1.156.0 and plugin v0.34.0 tested directly, and Trivy 0.76.1
- **Relates to:** ADR-011 (provider-neutral hosting), ADR-013 (log collection and redaction),
  ADR-014 (log retention)

## Context

P1-T9a–c gave the server metrics (Prometheus), e-mail alerts (Alertmanager), a redacted log
store (VictoriaLogs) and log alerts (vmalert). The operator, one person, reaches each of these
only through its own bare web page on the SSH tunnel. There is no single page that shows the
stack's health, and no easy way to search the logs. PLAN P1-T9d asks for dashboards and for a
decision on access.

## Decision

1. **Grafana OSS 13.3.1, unmodified, as a viewer** (Q-O13). It is our image
   `deploy/docker/grafana.Dockerfile`:
   - **The logs plugin.** VictoriaLogs datasource v0.34.0 is added at build time and checked by
     its SHA-256. Nothing is downloaded at run time.
   - **Bundled plugins.** All except the Prometheus datasource and the canvas panel are
     removed, and all others are disabled. Only two datasource types exist.
   - **No outside calls.** Every setting that calls out is off in the image's ENV: usage
     reports, update and plugin checks, plugin preinstall, the catalog, news, gravatar and
     external snapshots. Grafana's grafana.com addresses point at a dead end on the host
     (`127.0.0.1:9`), so a caller we missed fails locally. Public ("shared externally")
     dashboards are off, and the datasource
     proxy may reach only `prometheus:9090` and `logs-reader:8427`. CI starts the image on a
     network with no way out, fails on any logged lookup or dial failure (debug level), and
     reads every setting back through the API. That read-back is the main check.
   - **Browser and sessions.** A strict Content-Security-Policy: nothing from grafana.com or
     map tiles, never framed. Sessions last at most 12 h, and 1 h idle.
   - **No alerting.** Grafana's alerting is off. Prometheus, vmalert and Alertmanager stay the
     only alerting, so a broken Grafana never hides an alert.
2. **Dashboards and datasources come from git, read-only.**
   - `deploy/grafana/build_dashboards.py` (stdlib) writes four dashboards: server and stack;
     data and records; traffic and sign-in; logs. A test fails if the committed JSON differs.
   - Static tests check that every Prometheus metric a panel names is written by an exporter
     we run or by our own scripts, and that every log field comes from the pipeline. Without
     this, a panel could stay empty for ever.
   - **Log levels.** Host lines take their level from journald's priority. Container lines
     take it from the message (`level=error`, `ERROR`, a Python traceback), because Docker
     marks all of stderr as priority 3 and most services log normally to stderr.
   - The firing-alerts panels read both sources: `ALERTS` for Prometheus and
     `vmalert_alerts_firing` for the log rules. They also show how many log rules are
     running, so a dead vmalert never reads as all-clear.
3. **Grafana's network: its own internal one** (`dashboards`, 10.203.0.48/28).
   - Its members are Grafana (fixed .50, and the only address it listens on), Prometheus and
     logs-reader.
   - Nothing that takes traffic from the internet can reach it: Traefik, the web app,
     Keycloak and SeaweedFS. It reaches neither the log store itself nor Alertmanager.
   - Nothing leaves the network. The host itself is reachable through the bridge's gateway
     address, but only on the ports the server's firewall allows from anywhere (ufw INPUT
     policy, `deploy/host/bootstrap.sh`): 22, 80, 443 and 53, which face the internet anyway.
4. **A read-only door to the logs.** `logs-reader` is vmauth (Apache-2.0, the same release as
   vmalert).
   - It holds the log store's password. Grafana holds only its own login, `grafana-ro`.
   - `grafana-ro` may call `/select/logsql/…` and `/select/tenant_ids`. Every other path gets
     "missing route": inserts, flushes, deletes and VictoriaLogs's own page.
   - So Grafana can neither add lines to, nor remove lines from, the CERT-In record.
   - vmauth's own pages (metrics, flags, reload) need a key of its own
     (`secrets/logs-reader-key`). The log store's admin key, which can delete logs, stays with
     VictoriaLogs alone; a test fails if any other service mounts it.
5. **Access: the existing SSH tunnel only** (Q-O14).
   - `scripts/staging.sh ops-tunnel` adds `127.0.0.1:3000`. The browser opens
     `http://grafana.localhost:3000`.
   - Grafana answers only to that host name (`enforce_domain`). A page that uses another name,
     DNS rebinding included, gets a redirect and cannot sign in or guess the password.
   - Its cookie belongs to `grafana.localhost`, a different site from the other tunnel pages
     on 127.0.0.1. Grafana's CSRF check refuses their writes, and the smoke test proves it.
6. **Sign-in.** One admin account.
   - The name is generated (`ops-<random>`, with an e-mail address that cannot be guessed) and
     so is the password. Both are in `secrets/` (mode 600) and set on the first start.
   - Grafana counts failed sign-ins per name. A guessable name would let anyone who reaches
     Grafana keep the operator locked out.
   - There is no anonymous access, no sign-up and no basic auth on the API.
   - `scripts/staging.sh grafana-reset-admin` changes the password in the database and the
     file together. The new password goes in on stdin and never on a command line.
   - Single sign-on through the operator realm is the way forward for a team (Q-O14 B).
7. **Operations.**
   - **Start.** Grafana starts with monitoring and is part of `up`, `smoke` and the restamp
     list: changed provisioning recreates it. Its data volume keeps preferences only. The
     dashboards are rescanned every minute.
   - **Memory.** The limit is 512 MB; Grafana measured 270 MB idle.
   - **Deploy.** `deploy.sh` builds and ships the image, labelled with the Dockerfile's hash
     so an unchanged build is not resent. It also sends `deploy/grafana`.
   - **Smoke.** `scripts/check-dashboards.py` checks:
     - health, the host-name redirect, the admin login, that a wrong password and the default
       name are refused, and that basic auth is off;
     - the CSP, `/metrics` closed, and a cross-origin write refused;
     - every setting, both datasources healthy and the dashboards as in git;
     - that every metric a panel names exists in Prometheus;
     - that every panel's query runs and returns data. The exceptions are listed in
       `deploy/grafana/allowed-empty.txt`, each with a reason and the cases it applies to: always
       (nothing firing), a proof, no archive or backups on this site, or an archive with no
       whole day yet. `smoke` works out the cases from `.env`.
   - **Reachability.** The smoke test also checks:
     - the web app cannot reach Grafana;
     - Grafana reaches Prometheus (the control) but not the log store or Alertmanager;
     - Grafana's log login can read but neither insert nor flush, and without a login nothing
       answers (401). This probe runs from a one-off container, with the password on stdin;
     - the store's admin key is not mounted in logs-reader.

## Consequences

- **AGPL.** Grafana is used unmodified and reached over the network only (CLAUDE.md). Our image
  adds a plugin, removes optional bundled plugins and sets ENV; Grafana's code is untouched.
- **Accepted findings.** Trivy finds two kinds that cannot be reached in our setup:
  - x/net/html quadratic parsing, in bundled Prometheus and the plugin;
  - Go x509's distrusted intermediates, in the plugin, which uses plain HTTP inside.
  Both are path-scoped in `.trivyignore.yaml` and expire on 31-12-2026, which forces a fresh
  look.
- **The tunnel's other pages.** Prometheus, Alertmanager and vmalert on 127.0.0.1 still have
  no Host check, and Alertmanager accepts silences without a login. This is unchanged from
  P1-T9a, and the operator can now use Grafana instead. A follow-up could drop them from the
  default tunnel.
- **Proof on this machine.** It has no SSH server, so Grafana answers to its own address there
  (`IHR_GRAFANA_DOMAIN`, allowed only with `.local`). The tunnel path itself is proven on a
  server.

## Alternatives

Perses scored 72.5 % against Grafana's 91.5 % (no VictoriaLogs plugin, young logs support). It is
the runner-up if Grafana's licence or telemetry change. Using the stores' own pages alone fails
the task. Publishing Grafana through Traefik was rejected: it would put a new login page on the
internet.
