# P1-T15a independent code review (2026-11-13)

Two fresh Opus reviewers who did not write the code (correctness and tests; security, tenancy
and operations), then a third agent tried to refute each finding. Of 19 findings: 3 confirmed,
15 partly, 1 refuted (S2). The fixes were made by another agent, and a fourth agent checked each
fix against its failure scenario ("Verified" column).

| # | Finding | Verdict | Fix | Verified |
|---|---|---|---|---|
| S1 | The `PackApplyFailed` log alert could be forged by an anonymous request whose URL holds the line, and the label went into the mailed shell command | confirmed, medium | the rule reads only the start-up services, matches a whole anchored line with a slug pattern, and the mail no longer pastes the label into a command | yes (anchored line, start-up services only; a whole forged line in those services could still alert, but never inject) |
| C1 / S8 | A kept item could never be accepted once a later release left it unchanged | confirmed, medium | kept items follow the newest release | yes (tests: accept after an unrelated release, for an item and a removal) |
| C3 / S3 | "Version files are immutable" did nothing in CI and missed deletions and renames | confirmed, medium | CI compares the manifest with the push's base commit; the local hook refuses M, D, R, T | partly, then closed by Claude: the hook runs on every commit (`always_run`, pre-commit never lists deletions), and CI checks against every committed manifest (`--history`); tests for both |
| S4 | A hospital deferred at start-up was never retried or alerted | partly, low | three retries with back-off, then the alerted failed line | yes (a hospital busy for over about 50 s is alerted, not retried) |
| C2 | A row whose compose lacks `exclude` (CARE then fails every lookup) was adopted as current | partly, low | repaired with the pack's canonical compose (one audited write) | yes; the C4 change reopened it for a hospital's own edit, closed by Claude: the missing list is added empty, their content kept (test) |
| C4 | Whether a local edit was flagged depended on unrelated pending items | partly, low | an edit is flagged only when the pack has something new for that item (rule added to the design) | yes |
| C5 | A release with only spelling changes was never recorded, so every start re-checked it | partly, low | outcome `reached` recorded once | yes |
| C6 | The adoption test did not reproduce real hospitals (30 sets, not 31) | partly, low | test now starts from 30 | yes |
| C7 | An audit test assertion could not fail | confirmed, low | asserted inside the job, and in the database setting | yes |
| C8 | A malformed item failed each hospital separately | partly, low | items validated once, refused fleet-wide | yes |
| C9 / S5 | The demo seed applied packs to a live hospital without its lock | partly, low | lock taken; a busy lock is a clear error | yes |
| C10 | A kept row reverted to the pack's content stays kept | partly, low | no rule change (it would change Q-K25); field documented | yes (comment only) |
| S6 | Operator names were free text and `--startup` could be claimed by hand | partly, low | `packs:` prefix, `--operator` refused with `--startup`, a name pattern | yes; "startup" and "check" now reserved by Claude (test) |
| S7 | The full `--reason` went into the audit context | partly, low | not in the context; at most 300 characters; help text says no personal details | yes (personal details: help text only) |
| S9 | `pack-build.sh` let extra arguments override the commit, and could overwrite a file | partly, low | refused; no-clobber copy | yes; abbreviations (`--care`, `--ou`) refused and `allow_abbrev` off, by Claude |
| S2 | `--startup` exits 0 even when every hospital fails | refuted | — (every failure is recorded and alerted; fleet-wide faults still exit 1) | — |

Design attack: design.md ("After the attack"; 24 findings, 1 refuted, none fatal).
