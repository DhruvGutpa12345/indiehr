# P2-T0c design attack: findings and decisions

Date: 2026-11-24. Target: `docs/reviews/p2-t0c/design.md` (first draft). Lenses: security (Opus),
`egm:egm-design`, `egm:egm-zoom-out`, `egm:egm-implementation-readiness`. Decisions by the
orchestrator; owner decision Q-K170 = B (`docs/QUESTIONS.md`, 2026-11-24). "Where" names the
section of the revised design.

| Lens | Id | Severity | Finding (short) | Decision | Where |
|---|---|---|---|---|---|
| security | S1 | high | Gate reads the route from the committed match but the facility from the next location (`useCurrentFacilitySilently` → `usePathname` → `state.location` on a same-route move) | folded: facility only from the leaf; `useIhrAccess`/`useIhrPatientView` take an explicit facility; row 1 `loading` on mismatch; R4 proof | §1.4, §3.2, §3.4, §3.6, §3.7.8, §8 R4 |
| security | S2 | medium | Clinical routes outside the facility patient prefix default to `open` with no CI guard; Q-K170 listed only part of them | folded: Q-K170 B (organization pages gated), `OPEN_CLINICAL_IDS` (24 ids), clinical invariant with mutation cases | §3.3, §3.7.4, §9 |
| security | S3 | medium | Loader check greps only `loader:`; misses `loader()`, `loaderDeps`, `beforeLoad` fetches through `queryClient`, `pluginRoutes.tsx` | folded: patterns `\bloader(Deps)?\b` and `queryClient\|ensureQueryData\|prefetch(Infinite)?Query\|fetchQuery` in `src/routes/**` and `pluginRoutes.tsx`, one fixture per pattern | §1.1, §6.3, §8 R12 |
| security | S4 | low | Any failed match passes the whole page | folded: gate of the deepest match that did not fail | §3.2, §3.7.8, §8 R13 |
| security | S5 | low | Facility-less my-access query has no retry/refetch | folded: `useRestrictedAnywhere` with `retry: 3` and the 15 s `refetchInterval` (now used by the organization gate) | §3.4 |
| security | S6 | low | New upstream `_public` routes go unreviewed | folded: `PUBLIC_ROUTES` list with marks, set-equality test, after-sync checklist line | §4.4, §3.7.5, §7.5 |
| egm-design | D1 | medium | "No clinical route open" invariant covers one prefix; organization encounter pages bypass the encounter gate | folded (same fix as S2; organization gated by Q-K170 B) | §3.3, §3.7.3, §3.7.4 |
| egm-design | D2 | medium | `withIhrRouteTree` not idempotent | folded: pure `mergeChildren` (skip present ids, replace in place), tested twice | §4.1, §3.7.10 |
| egm-design | D3 | medium | Redirect routes depend on `update()`; fallback not named | folded: preferred = code routes with the same id whose `beforeLoad` throws `redirect`; fallback = `update({component: ToLogin})`; builder checks after `npm ci` | §4.1, §4.2 |
| egm-design | D4 | medium | No way to unblock a legitimate plugin page | folded in part: one sentence, plugin pages for restricted roles out of scope. Declined: `pluginAllow` list, since unknown ids now get the prefix rule or `open` (parity), so nothing is blocked that needs unblocking | §3.5 |
| egm-design | D5 | low | Failed-match pass-through asserted without citation | folded (same fix as S4; cut cited from `layoutFlags.ts:50-54`) | §3.2 |
| egm-design | D6 | low | Loader text check misses run-time plugin loaders | folded: stated as not covered | §6.3, §8 |
| egm-design | D7 | low | Scroll-restoration key likely random, keep as a check | folded: check only; change nothing if keys are random | §6.4 |
| egm-zoom-out | M1 | medium | 346-id JSON table adds per-sync friction; prefix rules give parity | folded: prefix rules on ids, `DEMOGRAPHICS_IDS` allow-list, `open` default; JSON table, exact-once and stale-entry checks over all ids, `tableProblems` dropped | §3.3, §3.7 |
| egm-zoom-out | M2 | medium | Fail-closed unknown ids presented as existing behaviour | folded: unknown ids get the prefix rule or `open` (parity); new vs parity stated | §3.5 |
| egm-zoom-out | M3 | low-medium | One fact checked in five places; CI `routeTree.gen.ts` diff fragile | folded: run-time self-check and the CI diff step dropped; unit test + checker anchor + build imports kept | §3.6, §7.3 |
| egm-zoom-out | L1 | low | `loader:` ban not needed for parity | declined: the security lens (S3) showed a fetch before the gate would bypass it on hover preload; the check is cheap and strengthened instead | §6.3 |
| egm-zoom-out | L2 | low | `authStatus.ts` must stay thin | folded: stated as a thin function over existing state | §5.1 |
| egm-zoom-out | L3 | low | `leafSignature` JSON could be two selectors | declined: the signature now carries route id, params, pathname and the failed flag of one match; one string selector is the simplest tear-free primitive (same idea as `AreaChrome.tsx:47-58`) | §3.2 |
| egm-readiness | M1 | medium | Pre-commit hooks reject commits A and B as planned | folded differently: no hook skipping (owner rule); the merge commit holds conflict resolutions and the port, so every hook passes; then lock commit, then docs | §7.1 |
| egm-readiness | M2 | medium | Facility source unstated (`useCurrentFacilitySilently` at the tip) | folded (same fix as S1; tip code quoted with file:line) | §1.4, §3.4 |
| egm-readiness | M3 | medium | How `routeGates.ts` gets the table in node tests | folded: no table; tests read `routeTree.gen.ts` text with `readFileSync`; `routeGates.ts` pure, relative imports only | §3.7 |
| egm-readiness | M4 | medium | `ihrRouteTree.tsx` snippet incomplete (pages table, hooks in arrows, idempotency, sidebar) | folded: inline five-page table, named `IhrPageRoute`, `mergeChildren`, sidebar choice cited (`layoutFlags.ts:42`, `app-sidebar.tsx:65-86`) + Playwright assertion | §1.3, §4.1 |
| egm-readiness | M5 | medium | No named round-trip proof; `safeNext` expected outputs missing | folded: `preflight.spec.ts` case via Keycloak; expected outputs table, hash dropped | §5.2, §10 |
| egm-readiness | L1 | low | Counts do not add up | folded: counts corrected and marked indicative; tests never pin them | §1.7 |
| egm-readiness | L2 | low | Failed-leaf pass-through needs a test | folded (same fix as S4, with outcome cases) | §3.2, §3.7.8 |
| egm-readiness | L3 | low | Scroll-restoration key check timing and file | folded: after `npm ci`, `router-core/dist/esm/scroll-restoration.js` | §6.4, §7.4 |

Not resolved in the design (left to the builder, with a stop rule): which redirect form TanStack
accepts (§4.2), whether the scroll-restoration key can hold an href (§6.4), and whether the dev
seed has a person with roles in two facilities for an end-to-end R4 case (§8).
