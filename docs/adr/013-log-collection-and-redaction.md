# ADR-013: Log collection, redaction and log alerts

- **Status:** Accepted
- **Date:** 2026-11-12
- **Decided by:** product owner (Q-O2, Q-O5…Q-O9), on Fluent Bit 5.2.0, VictoriaLogs v1.53.0 and
  vmalert v1.156.0 read from their source and docs
- **Relates to:** ADR-011 (provider-neutral hosting), P1-T8d1 (Keycloak events, per-IP limits),
  P1-T9a (Prometheus, Alertmanager), P1-T9c (180-day record: daily locked archive)

## Context

Every container logs to the host's journal (Docker's journald driver), and the host itself
logs SSH, sudo, the firewall and systemd there too. The journal keeps 180 days, the CERT-In
minimum. Nothing collected these logs, nothing could search them, and nothing alerted on them.
Some lines carry personal data or secrets:

- Keycloak's sign-in events record whatever was typed as the user name, even when no account
  matched. That can be a password typed into the wrong box.
- nginx error lines and Keycloak admin-API errors keep query strings, which hold searches, codes
  and URL-encoded e-mail addresses.
- psycopg errors echo database values.

The research inventory of every service's log output is in the P1-T9b tracker row. It also found
that CARE's deployment logging switched off gunicorn's and Django's loggers, so on staging a
server error left no trace at all.

## Decision

1. **Fluent Bit reads the whole server's journal** (owner, Q-O5, Q-O6):
   - It runs as `nobody` with the host's `systemd-journal` group, from a read-only mount. It
     cannot reach the Docker socket or the rest of the host.
   - It keeps its read position and its unsent lines on a volume of its own.
   - Every compose service's journald logging carries the label `com.docker.compose.service`,
     and the host's Docker default does too. Lines are therefore stored by service, not by
     container name.
2. **Redaction happens before anything leaves Fluent Bit** (owner, Q-O7), in
   `deploy/logs/fluent-bit/redact.lua`:
   - Only a short allow-list of journald fields is kept. Command lines, capabilities and IDs
     are dropped.
   - A line counts as a container's only when journald's trusted fields say Docker wrote it
     (`_TRANSPORT=journal`, `_COMM=dockerd`, `_UID=0`). Any host process can add
     `CONTAINER_NAME` or a service label to its own entries. Such entries stay host lines and
     never pass for Keycloak's or Traefik's. Only container lines get event fields, and the
     log alerts read only those.
   - Keycloak lines are parsed strictly. This follows Keycloak 26.8's escaping, which escapes a
     quote but never a backslash:
     - every value must be quoted;
     - a backslash escapes only a following quote;
     - no key may repeat;
     - the fixed head (type, realm, client, user[, session], address[, error]) must come first.

     Event fields come from that head only, so text typed into the username box cannot forge
     `type`, `ipAddress` or `error` (P1-T9b review). A line that fails these checks loses
     everything from `username=` to its end, and gets no event fields.
   - Keycloak event lines are parsed and rebuilt from an allow-list of keys. Unknown keys are
     dropped, and their names are recorded in `kc_dropped`.
   - The typed user name is dropped when no account matched (`user_not_found`). A real
     account's user name is kept, for lock-out checks.
   - E-mail details become a marker. Query strings are cut from `redirect_uri`.
   - Every other string is masked:
     - tokens (JWTs, Bearer/Basic, `key=value` secrets);
     - query strings, including relative ones (`users?email=`). Our Keycloak admin errors no
       longer include the query at all;
     - database values in error texts, from the marker to the end of the line;
     - e-mail addresses, including URL-encoded ones;
     - mobile numbers in their usual shapes;
     - 12-digit (Aadhaar-like) numbers;
     - ABHA numbers and addresses;
     - user names in API paths.
   - IP addresses, UUIDs and timestamps stay. CERT-In needs the addresses, and the others are
     join keys.
   - A Lua error drops that one line (protected mode). Fluent Bit counts the drop and Prometheus
     alerts on it. A line is stored masked or not at all.
   - The masks are tested by running them: `scripts/check-logs.py` passes 25 cases through the
     pinned Fluent Bit and a throwaway VictoriaLogs.
3. **VictoriaLogs stores the redacted lines** (owner, Q-O2):
   - It keeps 180 days, capped at `IHR_LOGS_MAX_SIZE` of disk. P1-T9c sizes the cap from
     measured volume and adds the daily locked archive.
   - It sits on an internal network shared only with Fluent Bit, vmalert and Prometheus, so
     the web app, Traefik and the exporters cannot reach it.
   - It asks for a password even for reading: user `ihr-logs`, password in
     `secrets/logs-http-password`. It sends no CORS headers. A web page open in the operator's
     browser while the SSH tunnel runs can neither read it cross-origin nor through DNS
     rebinding (P1-T9b review).
   - `/metrics` has its own low-value key, which Prometheus's target file carries.
   - Deleting logs, managing partitions, forcing merges and reading its flags all need a key
     in `secrets/logs-admin-key`, which only the operator holds.
   - The operator reads the logs through the SSH tunnel.
4. **The log alerts run on vmalert** (owner, Q-O8). It runs LogsQL rules
   (`deploy/logs/alerts/`) every minute and hands their results to the existing Alertmanager,
   so they are mailed like metric alerts:
   - distinct accounts locked out from one address;
   - a lock-out storm across addresses;
   - credential failures from one address;
   - an address refused with 429 for at least 5 minutes.

   The mail names the address (`client_ip`). That field is checked to be an IP address before
   it is stored. The three per-address alerts are mailed at any hour (owner, Q-O9). Other
   warnings still wait for the day.
5. **The pipeline is watched by Prometheus:**
   - redaction drops;
   - shipping errors;
   - lost lines;
   - an ingest stall;
   - rejected lines;
   - a read-only store;
   - the store near its cap;
   - failing log alerts.

   Each is unit-tested with promtool.
6. **Backend loggers stay on.** The platform's deployment settings set
   `disable_existing_loggers` to `False`, so gunicorn's path-only access log, Django's request
   errors and Celery's task results reach the journal, where they are redacted like any other
   line.

## Consequences

- One place to search the server's logs, already redacted, and alerts on sign-in abuse.
- RAM: about 120–200 MB more. Fluent Bit's limit is 128 MB, VictoriaLogs' 256 MB and vmalert's
  64 MB.
- The journal itself still holds the raw lines until P1-T9c shrinks it to a 14-day buffer
  (owner, Q-O3). Until then, the redaction protects only what leaves the journal.
- The masks are patterns. A value in an unusual shape can pass, and an ordinary number can be
  masked. Both are tested in the cases file, and a new shape becomes a new case.
- A real staff user name (an e-mail address) is kept in sign-in events, by the owner's choice.
  Every other e-mail address is masked.
- Fluent Bit 5.2.0 rewrites its journal cursor file on every collect cycle that finds new lines
  (upstream issue #12778, in every version). That costs disk writes only, with no data effect.
  Its own per-flush info line, which kept the journal from ever going quiet, is off
  (`log_level: warn`). Watch for the upstream fix.
- A first start on a server reads the whole existing journal once (up to 180 days). A proof on a
  desktop starts at the journal's end (`IHR_LOGS_FROM_TAIL=on`).
