# P1-T9d independent review (2026-11-12)

Three Opus reviewers looked at the built work, each through one lens: security, correctness and
operations, and test quality. None of them wrote the code. A separate Opus skeptic then tried to
refute each lens's findings, using the code and read-only checks on the running proof.

- **Found:** 19.
- **Confirmed or partly confirmed:** 17.
- **Refuted:** 2.

Every surviving finding is fixed, and the proof was run again (see TRACKER).

| ID | Finding | Verdict | Fix |
|---|---|---|---|
| SEC-1 | logs-reader mounted the log store's admin key (it deletes logs) for its own pages | partly, low | vmauth has its own `secrets/logs-reader-key`. A test checks that the admin key is mounted into VictoriaLogs only, and smoke checks it is absent from logs-reader |
| SEC-2 | Grafana reaches the host through the bridge gateway; the ADR said "nothing outside" | partly, low | Only the ports ufw allows from anywhere are reachable. The ADR is reworded, and the datasource proxy may reach only `prometheus:9090` and `logs-reader:8427` |
| SEC-3 | Public ("shared externally") dashboards were on | confirmed, low | `GF_PUBLIC_DASHBOARDS_ENABLED=false`, checked in the settings read-back and the static test |
| SEC-4 | Smoke put grafana-ro's password on wget's command line | partly, low | The probe runs from a one-off container with the password on stdin. It also checks that without a login nothing answers (401) |
| F1 | A VM proof (`deploy.sh --local-proof`) never got the Grafana image | confirmed, medium | deploy.sh builds and ships it for every local proof |
| F2 | Smoke failed on a new server, or one without backups or an archive | confirmed, medium | `allowed-empty.txt` takes cases (always, proof, archive-off, archive-new, backup-off); smoke works them out from `.env` and Prometheus |
| F3 | Prometheus tables lack a merge step | refuted | Grafana's labelsToFields always merges |
| F4 | "Error-level" and the red log colour came from Docker's stderr priority | confirmed, medium | Host lines use journald's priority, container lines the level in the message; checked against the live store |
| F5 | The recovery advice in dashboards_secrets could not work | confirmed, low | The message now says: restore the files, or remove the volume (preferences only) |
| F6 | A local proof never rebuilt a changed Grafana image | confirmed, low | grafana_image compares the image's Dockerfile-hash label and rebuilds |
| T-F1 | Always-empty panels had no positive check | partly, medium | Live: every metric a panel names must exist in Prometheus. Static: exporter names must be in `metrics-seen.txt`, and LogsQL fields must match the pipeline's exact names |
| T-F2 | The "bind sources are shipped" test could never fail (its regex caught 960 tokens) | confirmed, medium | The regex is anchored to the stack-files call, every token must be a real path, and the needed entries are required |
| T-F3 | The "no log-store password in Grafana" test only checked two names | partly, low | It checks Grafana's exact volume list, and that no overlay adds `secrets`, `configs` or `volumes_from` |
| T-F4 | The vmauth test did not reject extra keys (`unauthorized_user`) | partly, low | Exact key sets; smoke probes without a login |
| T-F5 | The bind test skipped short-syntax mounts | partly, low | A `../` path in short syntax fails |
| T-F6 | Hardening checks were positive-only | partly, low | `privileged`, `pid`, `ipc`, `devices`, `ports` and similar keys are refused for the three services in every overlay |
| T-F7 | Metric "emitted" also matched queries in staging.sh | confirmed, low | Only emission forms count: `describe`/`family`/`metric` and `# TYPE` |
| T-F8 | The CI egress grep read info-level logs only | partly, low | The CI container runs at debug; the ADR's wording now matches |
| T-F9 | The "cannot reach" check had no positive control | refuted | The log-door probe already fails when exec is broken. Reaching Prometheus was added as an explicit control anyway |

The mutation check, run after the fixes: each of the reviewers' example mutations now fails the
static tests. These were removing the deploy/grafana entries from deploy.sh, `kc_err`,
`stats by (clientip)`, `traefik_routr_…`, `unauthorized_user`, mounting `logs-reader.env` into
Grafana, `privileged: true` and renaming an emitted metric.
