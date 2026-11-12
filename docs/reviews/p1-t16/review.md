# P1-T16 independent code review (2026-11-13)

Two fresh Opus reviewers who did not write the code (isolation strength; robustness and
operations), then a third agent tried to refute each finding. Of 11 findings: 1 confirmed, 7
partly, 3 refuted (I5, R5, R6). Every confirmed or partly confirmed finding is fixed. The isolation
suite was then run twice with identical crawl counts.

| # | Finding | Verdict | Fix |
|---|---|---|---|
| R1 | An anonymous allow-listed route answered 200 in some runs and 404 in others. Cause: CARE's demo fixtures make the first facility public at random | partly, low | the world makes the first facility public; the test asserts that route is open every run |
| I1 / R3 | Converter values were chosen by parameter name, so a future `<int:pk>` route would be "crawled" with an unrelated id | partly / confirmed, low | the set of converter routes the crawl fills must equal the reviewed list |
| I2 | The fail-closed check let leftover regex syntax (`[0-9]+`, `\d`, …) through as a literal path | partly, low | any leftover regex metacharacter counts as skipped (0 existing routes lost: 213 of 214 filled before and after) |
| I3 | "Own id or canary" was always true through nested names, so canary placement was never checked | partly, low | 27 of 31 seeded routes now require the canary in the exact field it was put in (the other 4 have no free-text field) |
| I4 | The audit-entry probe never proved it was the hospital's own entry | confirmed, low | it uses the entry the canary step wrote and asserts own-ness |
| R2 | One CARE model change during an upstream sync would error the whole suite with an unhelpful message | partly, low | each seeding step names its model ("CARE model drift?"); the report template is chosen explicitly |
| R4 | The mutation-cost comment was stale | partly, low | corrected: one full-stack job per row, 26 today |
| I5, R5, R6 | (geography org as audit admin; PIN load without force; a 2030 date) | refuted | — |

Design attack: design.md ("After the attack": 11 findings, 3 refuted, none fatal).
