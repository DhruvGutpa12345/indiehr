# ADR-014: The 180-day log record and the daily locked archive

- **Status:** Accepted
- **Date:** 2026-11-12
- **Decided by:** product owner (Q-O3, Q-O10, Q-O11, Q-O12), on VictoriaLogs v1.53.0's API tested
  directly, E2E Object Storage's and AWS S3's documentation, the CERT-In Directions of 28-04-2022
  with their FAQs, and the DPDP Rules 2025
- **Relates to:** ADR-010 (audit trail, its locked fingerprints), ADR-011 (provider-neutral
  hosting), ADR-013 (log collection and redaction)

## Context

ADR-013 put every line of the server's journal, redacted, into VictoriaLogs on the server. Two
rules decide how long logs must last:

- **CERT-In direction (iv):** logs of all ICT systems are kept "securely for a rolling period of
  180 days", within Indian jurisdiction.
- **DPDP Rules 2025, rules 6(1)(e) and 8(3):** logs of processing are kept for at least a year,
  then erased unless another law needs them. These rules take force around May 2027.

Until now the journal itself kept a year of raw, unredacted lines, on the server only. If the
server is lost, the record goes with it.

## Decision

1. **The journal becomes a 14-day buffer** (owner, Q-O3). It is set to `MaxRetentionSec=15day`
   with one file a day, so at least 14 whole days are kept. The size cap is 8 GB, about three
   times the measured volume (README).

   Raw lines therefore last two weeks, not a year. The record is made of the two parts below,
   both built from redacted lines.
2. **The log store keeps 180 days** (VictoriaLogs `-retentionPeriod=180d`, within
   `IHR_LOGS_MAX_SIZE`). It is the searchable record.
3. **A daily locked archive keeps 400 days** in the backup region (Q-O3). That is a year plus a
   margin for leap years and for discovering an incident late.
   - **The archiver** (`deploy/logs/archive/archive.py`, from the backend image, on the `logs`
     network and its own egress bridge) handles each India day (00:00–24:00 IST) once it has
     been over for an hour.
   - **Export:** hour by hour from VictoriaLogs. Each hour's count is taken before and after
     the export, because VictoriaLogs ends a slow query early with no error.
   - **Format:** gzip of the JSON lines, then encrypted on the server with our own key
     (Fernet: AES-128-CBC + HMAC-SHA256, from `cryptography`, in 1 MiB chunks; owner, Q-O11).
   - **Upload:** written once (`If-None-Match`, Content-MD5) into a bucket with Object Lock and
     a default retention of 400 days, then read back and checked.
   - **Manifest:** a small manifest goes beside each day. It lists:
     - lines per hour;
     - the object's SHA-256;
     - the SHA-256 of the manifest before it, forming a chain;
     - an HMAC with a key derived from the archive key.

     The chain is followed by its links, not by time: one root, no forks, no gaps. A missing,
     replaced or forged day therefore shows.
   - **Late lines:** every day still in the store is recounted on every pass. A day that gained
     lines, because Fluent Bit delivered a backlog late, is archived again as the next revision.
     The earlier revision stays.
   - **Revisions only grow:** a genuine later revision re-exports the same day, so it holds every
     line of the one before. `check` compares the hourly counts, and the weekly full check (and
     `check --deep`) the lines themselves. A cleaned copy written with the server's keys is
     therefore caught (P1-T9c review).
   - **Interrupted uploads:** a name already taken (a file stored before a crash, without its
     manifest) is skipped for the next revision, so one failure never stops the archive.
   - **Archive key:** without it nothing can be read. It goes into the owner's password manager.
4. **Where the archive lives:**
   - **Go-live:** a new E2E Chennai bucket next to the backups (owner, Q-O10).
   - **E2E's lock:** Governance mode only, so E2E's account admin could still lift it. This was
     already accepted for the audit fingerprints (Q-E7).
   - **E2E's writer key:** E2E documents no delete-free writer role. The writer key's real
     rights are checked at go-live (README).
   - **Proofs:** the local stand-in (SeaweedFS) holds the same Governance lock, with a key that
     can add and read under `logs/` and nothing else. `s3-check` proves that.
   - **Later:** AWS Hyderabad, with COMPLIANCE lock and a put-only key, remains a settings
     change.
5. **Erasure after 400 days** (DPDP rule 8(3); owner, Q-O12):
   - `scripts/staging.sh logs-archive-expire` runs monthly from the owner's machine, with the
     bucket's admin key typed in. It deletes every version of the days past 400, bypassing the
     Governance lock.
   - It refuses fewer than 400 days, refuses a clock more than an hour off the bucket's, and
     skips any file whose own lock still runs.
   - **Before deleting,** it writes an erasure record listing the days, the objects, and each
     erased manifest's hash with its predecessor. The chain is followed through them. The
     record is authenticated with a key derived from the archive key: only the owner's machine
     can make one, and the erasure tool never sees the archive key itself.
   - The server never holds a key that can delete.
   - LogArchiveErasureOverdue warns when the oldest day is more than 435 days old.
6. **Watched:** the archiver serves its own metrics. Prometheus alerts on:
   - LogArchiveBehind: more than a day behind (warning), more than four days (critical);
   - LogArchiveFailing: failing passes;
   - LogArchiveNotAuthentic: a manifest or erasure record that is not ours, a broken chain, or a
     revision that drops lines (critical);
   - LogArchiveNotLocked: the bucket no longer locked for 400 days with versioning (critical);
   - LogArchiveShrunk: a recent day that shrank in the store;
   - LogArchiveErasureOverdue: overdue erasure.

   `smoke` runs `logs-archive-check --recent 2`. `logs-archive-probe` shows what the server's
   key may do in the real bucket (go-live: E2E documents neither its Writer role's rights nor
   whether a key may read the lock settings).

## Consequences

- If the server is lost, at most the current day and the hour of grace are lost from the
  archive; the journal buffer and the store go with the server. The archive is in another region,
  encrypted and locked.
- The record holds redacted lines only (ADR-013). IP addresses, account ids, actions, outcomes
  and times survive, which is what incident analysis needs. Patient data and secrets do not.
- **The owner holds two more secrets:** the archive's encryption key, and (at go-live) the
  archive bucket's admin key. Without the first the archive is unreadable. The second is needed
  only for the monthly erasure.
- **E2E's Governance lock protects against a stolen server key, not against E2E's account
  admin.** The chain and the HMAC still make a changed or removed day visible to
  `logs-archive-check`.
- **Shortening an old journal loses its unshipped lines.** On a server with months of journal,
  the log stack must catch up before the 14-day setting is applied (host README).
- **Not covered here:** retention of the audit trail itself (ADR-010). It must also meet the
  DPDP one-year minimum; that is separate from this archive.
