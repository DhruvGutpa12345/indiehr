# P2-T0c design: merge the web app to the tip and port our router integration to TanStack Router

Status: design for the builder, revised 2026-11-24 after the design attack (four lenses:
security, egm-design, egm-zoom-out, egm-implementation-readiness; findings and decisions in
`docs/reviews/p2-t0c/attack.md`). Owner decisions: Q-K162 (the port is done in this task;
afterwards `apps/frontend` sits at upstream `91b028f3aa33`, 2026-11-23) and Q-K170 = B (§9).

Read-only facts were taken from `git show upstream-frontend/develop:<path>` (paths relative to the
app root) and from our working tree at `6ba2c9be8`. File:line citations of upstream files are at
`91b028f3aa331e4c9e185b883fc3edc3db373019`. "Ours" means `apps/frontend/...` today.

## 0. Summary

- **Hook point (§3):** one wrapper, `IhrStaffPage`, placed by a two-line marked patch in
  `src/Routers/AppShell.tsx` around `{children}` (the staff page outlet). It reads the **rendered
  (committed) matches** (`useMatches`), takes the **deepest match that did not fail**, and gives
  its **route id** (`/_app/facility/$facilityId/...`) a gate by **prefix rules** on route ids
  (`src/Utils/ihr/routeGates.ts`, pure). It wraps the page in the one grant watcher and the gate.
  The gate and the watcher take the facility **only from that match**, and answer `loading` while
  the address bar already names another facility (§3.4 row 1).
- **Classification (§3.3):** today's rules, translated to route ids: patient prefix →
  `patientClinical` except a reviewed `DEMOGRAPHICS_IDS` allow-list; encounter prefix →
  `encounterPage`; the encounter page, bed board, lists and patient-tab rule as today. Per
  Q-K170 B the same rules cover `/_app/organization/<any>/patient/...`. Everything else is `open`
  (parity), and a reviewed `OPEN_CLINICAL_IDS` list names every open route that shows patient or
  visit data; a CI invariant fails on any new such id that is neither gated nor listed.
- **Our pages (§4):** code-based routes added under `/_app` before the router is built, by one
  marked call in `src/router.tsx` (`withIhrRouteTree(routeTree)`, idempotent).
  `src/routeTree.gen.ts` stays byte-identical to upstream's. The same call replaces `/2fa`,
  `/forgot-password` and `/password_reset/$token` with redirect routes to `/login`.
- **Sign-in (§5):** `AuthUserProvider.tsx` is re-ported: our cookie session feeds upstream's
  `routerAuth` through `setAuthStatusResolver`. A pure `authStatus.ts` decides the status for both
  the router guards and the rendered chrome.
- **Merge (§7):** the sync stops with exit 2 and leaves the merge uncommitted; the builder
  resolves the conflicts, does the port and runs the local proofs in that same working tree, then
  commits the merge **once** with every hook running. Then a commit pinning `upstream.lock`, then
  docs. No hook is ever skipped.

## 1. Upstream's router at the tip

### 1.1 Creation and context
- `src/router.tsx:18-31`: `createRouter({ routeTree, context: { queryClient, auth: routerAuth },
  defaultPreload: "intent", defaultPreloadStaleTime: 0, scrollRestoration: true, parseSearch,
  stringifySearch, pathParamsAllowedCharacters: [","], defaultNotFoundComponent })`, then
  `setRouter(router)` (line 33) so `getRouter()` works outside React (`src/lib/router/routerRef.ts`).
- `src/App.tsx:47-49`: `<AuthUserProvider><RouterProvider router={router} /></AuthUserProvider>`.
  The provider sits **outside** the router; it takes only `children`.
- Search strings stay plain strings (`src/lib/router/searchParams.ts:12-25`).
- No file under `src/routes/**` and nothing in `src/lib/router/pluginRoutes.tsx` contains
  `loader`, `loaderDeps`, `queryClient`, `ensureQueryData`, `prefetchQuery`,
  `prefetchInfiniteQuery` or `fetchQuery` (checked with `git grep -nE` at the tip: no hit). Routes
  use `beforeLoad` only (guards and redirects). So preloading on hover fetches no data.

### 1.2 Areas and guards (`src/lib/router/auth.ts`)
- `AuthStatus = "loading" | "user" | "patient" | "anonymous"` (line 13); `Area = "app" | "patient" |
  "public"` (16).
- `routerAuth.status` is read through a resolver that `AuthUserProvider` registers:
  `setAuthStatusResolver(resolver)` (36-38). The provider calls `getRouter().invalidate()` when the
  status changes (upstream `AuthUserProvider.tsx:129-137`), so guards run again.
- `redirectToLogin(location)` (81-87): `/login?redirect=<window.location.origin + location.href>`
  (absolute URL), `replace: true`.
- `requireStaff` (90-99) on `src/routes/_app.tsx:9-11`: loading returns `{}`, user returns
  `{ area: "app" }`, anything else throws `redirectToLogin`.
- `requirePatient` (102-116) on `_patient.tsx`: staff get `notFound()`.
- `publicOnly` (119-128) on `_public.tsx:6-8`: staff are redirected to `/` keeping the query.
- `staffOrPublic` (131-140): `/`, `/licenses`, `/session-expired`. `staffOrPatient` (143-157):
  `/patient/$id/{-$tab}`, `/patient/home`, `/referrals/$referralId/...` (root-level shared URLs, not
  under `_app`).

### 1.3 Rendering: root, area chrome, shells
- `src/routes/__root.tsx:54-74`: `RootComponent` renders `<AreaChrome area>` around
  `<PreviousPageGuard><Outlet/></PreviousPageGuard>` once `useResolvedArea()` is known, else
  `<Loading/>`; it also mounts `<PluginRouteAttacher/>`.
- `src/Routers/AreaChrome.tsx:44-68` `useResolvedArea()`: the area of the deepest match that set
  one; renders only when `isAreaAllowed(area, status)`. Lines 79-94: `"app"` → `<AppShell>`,
  `"patient"` → `<PatientShell>`. So a staff page never renders without a staff user.
- `src/Routers/AppShell.tsx:81-83`: the page is `{children}` inside
  `<ErrorBoundary fallback={<ErrorPage forError="PAGE_LOAD_ERROR"/>}>`, inside `PermissionProvider`.
  **This is our hook point (§3).**
- Sidebar choice (needed for our pages, §4.1): `AppShell.tsx:28-34` reads `useLayoutFlags()`,
  whose defaults are `{ sidebar: true, navigation: "facility" }` (`src/lib/router/layoutFlags.ts:42`);
  `src/components/ui/sidebar/app-sidebar.tsx:65-86` shows the facility nav when the path matches
  `/facility/:facilityId` (`useRouteParams`, `src/hooks/useRouteParams.ts:49-52`, a prefix match on
  `usePathname()`) and has no `locations/:locationId` or `services/:serviceId` segment. There is no
  `/_app/facility/$facilityId` layout route, so CARE's facility pages are direct children of `/_app`
  just as ours will be.

### 1.4 Committed matches vs the location (the reason for §3.4 row 1)
- `src/lib/router/previousPage.ts:15-29` `isShowingPreviousPage(router, state)`: false when the
  deepest committed match's pathname equals `state.location.pathname`, **and also false when the
  next location resolves to the same route id** (line 27-28: `route?.id !== deepest.routeId`).
- `src/lib/router/location.ts:24-33` `useCurrentLocation()`: `state.resolvedLocation` only while
  `isShowingPreviousPage`, otherwise `state.location` (the **next** location). `usePathname()`
  (36-38) is its `pathname`.
- `src/pages/Facility/utils/useCurrentFacility.tsx:30-44` `useCurrentFacilitySilently()`:
  `extractFacilityId(usePathname())` (lines 12-20: segment 2 after `/facility/`).
- Consequence: on a navigation from `/facility/A/patient/p/encounter/e/updates` to
  `/facility/B/patient/q/encounter/f/updates` (same route id), `useCurrentFacilitySilently()`
  already says B while `state.matches` (and the `<Outlet/>`) still render A's page until the new
  matches commit. Today `useIhrAccess()` and `useIhrPatientView()` take the facility from that
  hook (`src/Utils/ihr/useIhrAccess.ts:28-29,96`). §3.4 removes this mix.

### 1.5 Plugin routes, splat, not-found
- `src/lib/router/pluginRoutes.tsx:17`: plugin routes attach under `/_app` at run time
  (`attachPluginRoutes`, 110-166: keeps the base children, `parent.addChildren([...base, ...plugin])`,
  `router.setRoutes(router.buildRouteTree())`). Core paths win over plugin paths (140-145).
- `src/routes/_app/$.tsx:10-12`: staff splat → `PluginPathNotFound` (spinner until manifests load).
- `defaultNotFoundComponent` (router.tsx:30) and `src/routes/_app/not-found.tsx`.

### 1.6 What replaced raviger
| raviger | tip replacement |
|---|---|
| `Link href` | `HrefLink` (`src/lib/router/HrefLink.tsx:42-66`, plain `<a>`, left click → router) or typed `Link to/params` from `@tanstack/react-router` |
| `navigate(url, {replace})` | `navigate({ href, replace })` or `navigate({ to, params })` (`src/lib/router/navigate.ts:60-91`); cross-origin href → `location.assign` |
| `useQueryParams()` | `useQueryParams(schema?)` (`src/lib/router/queryParams.ts:61-87`), same `[params, setParams(query, {overwrite=true, replace=false})]` shape; without a schema every value is a string |
| `usePath()` | `usePathname()` / `useCurrentLocation()` (`src/lib/router/location.ts:24-38`) |
| `useLocationChange` | `useLocationChange` (`location.ts:45-55`) |
| `<Redirect to>` | `throw redirect(...)` / `redirectKeepingQuery` in `beforeLoad` (`auth.ts:67-75`), or `<Navigate to replace/>` at render |
| `useRoutes(table)` | file routes in `src/routes/**` → generated `src/routeTree.gen.ts` |

### 1.7 Generated tree
- `vite.config.mts:443-452`: `tanstackRouter({ routesDirectory: "./src/routes", generatedRouteTree:
  "./src/routeTree.gen.ts", routeFileIgnorePrefix: "-", autoCodeSplitting: false, quoteStyle:
  "double", semicolons: true })`, before `react()`.
- `src/routeTree.gen.ts` (about 8,400 lines) **is committed upstream**, excluded from eslint
  (`eslint.config.mjs:65`) and prettier (`.prettierignore:5`).
- Its `export interface FileRoutesById` (line 3304) lists every route id plus `__root__`. Counts
  at the tip, **indicative only** (tests never pin them): 346 ids; `/_app/facility` 244,
  `/_app/admin` 35, `/_app/organization` 24, `/_public` 9, `/_patient` 7, `/_app/patient` 7,
  root-level shared 7, `/_app/responsibilities` 5, the rest of `/_app` 8.
- **Route id vs fullPath:** ids keep pathless layouts (`/_app/facility/$facilityId/...`,
  `/_public/facility/$id`, `/_patient/facility/$facilityId/...`); fullPaths drop them. Index routes
  end in `/` (`/_app/facility/$facilityId/patients/`). **We key everything on route ids.**
- Upstream's literal segment: `/_app/organization/organizationId/patient/$patientId/encounter/$encounterId/consents/$consentId`
  (no `$`). Every organization rule below matches **any** segment after `/_app/organization/`.

## 2. Our integration today, and its fate

| Piece (ours) | Today | After P2-T0c |
|---|---|---|
| `src/Routers/AppRouter.tsx` patch: `withIhrRoutes({...})` | wraps CARE's raviger table by key | file deleted upstream; `git rm`; replaced by §3 + §4 |
| `src/components/ihr/IhrRoutes.tsx` (`wrap`, `withWatcher`, `OUR_ROUTES`, `reportMissing`) | gates + watcher per key | deleted; replaced by `IhrStaffPage.tsx` + `ihrRouteTree.tsx` |
| `src/Utils/ihr/routeKeys.ts` + test (`routeGate`, `watcherScope`, `DEMOGRAPHICS_KEYS`, prefixes, `upstreamKeys()` regex over `Routers/routes/*.tsx`) | key rules | rewritten as `routeGates.ts` + `routeGates.test.ts` (§3.3, §3.7): same prefix-rule shape, on route ids |
| `src/components/ihr/IhrGates.tsx` (4 gate components with `render`) | per-gate components | one `IhrRouteGate` component (§3.4); outcome functions stay pure |
| `IhrGrantWatcher.tsx` (`facilityLess` prop, raviger `navigate`) | wrapped per route | `scope` + `facilityId` props from the leaf, always mounted in `IhrStaffPage`; `navigate({href, replace:true})` |
| `src/Utils/ihr/useIhrAccess.ts` (`useIhrAccess()`, `useIhrPatientView(patientId)`) | facility from `useCurrentFacilitySilently()` | optional explicit facility: `useIhrAccess(facilityId?)`, `useIhrPatientView(patientId, facilityId?)`; CARE screens that call them without it keep today's behaviour |
| `src/Routers/PublicRouter.tsx` patch (`/2fa`, `/forgot-password`, `/password_reset/:token` → `/login`) | raviger `<Redirect>` | file deleted upstream; `git rm`; redirect routes in `withIhrRouteTree` (§4.3) |
| `AuthUserProvider.tsx` patch | cookie session, props `unauthorized`/`otpAuthorized` | re-ported onto upstream's shape (§5) |
| `Login.tsx` patch (staff panel = `IhrStaffSignIn`) | raviger `useQueryParams` | upstream's version + same patch (§5.3) |
| `IhrStaffSignIn.tsx`, `IhrAuditTrail`, `IhrOwnerTotals`, `IhrPackUpdates`, `IhrRecordsAccess` | raviger `useQueryParams` | `@/lib/router/queryParams` |
| `IhrEncounterStatus`, `IhrNotForRole`, `IhrMyPlaces`, `IhrPatientActions`, `IhrRecordsAccess` | raviger `Link` | `HrefLink` |
| `errorHandler.ts`, `usePatientSignOut.ts`, `AccountShow.tsx`, `LocationOverview.tsx` hunks | raviger in our hunks | upstream's idiom in that file |
| Menus (`menu.ts`, `ihrMenu.tsx`, `IhrNavMain`, `IhrNavTabs`) | URL strings into `NavigationLink.url` | unchanged: `nav-main.tsx` still takes `url: string` (tip line 57) |
| `IhrHospitalsMenu` (`window.location.assign`), `staffSession.ts` sign-in/out (full page loads) | no router use | unchanged |
| `scripts/check-ihr-frontend.py` §2 route keys, §3 `AppRouter.tsx` hook, §3c watcher in `IhrRoutes.tsx` | anchors in deleted files | re-anchored (§6.3) |
| `tests-ihr/lib/fixtures.ts` `watchRouteErrors` ("ihr-routes") | `withIhrRoutes` missing key | nothing emits `ihr-routes` any more (§3.6): drop that prefix and its comment |

`grep -rln '"raviger"' apps/frontend/src | xargs grep -li 'ihr\|IndieHR'` lists 38 files: our 11
own files above plus 27 CARE files we patched; in those, only the hunks above are ours.

## 3. Route gates on the new router

### 3.1 Choice
`IhrStaffPage` wraps the staff page in `AppShell.tsx` (patch: one marked import, and `{children}` →
`<IhrStaffPage>{children}</IhrStaffPage>` inside the `ErrorBoundary`). Reasons:
- One upstream file, two lines, no edits to any route file.
- `AppShell` renders only for area `"app"`, i.e. only with a loaded staff user, so `useAuthUser()`
  is safe inside, and patient/public pages are never touched. It covers `_app/**` **and** the
  root-level shared routes rendered for staff.
- Inside the `ErrorBoundary`: a crash in our gate shows CARE's error page, never the page.

Rejected: a `beforeLoad` in `_app.tsx` (cannot see root-level shared routes; access needs async
queries per patient; the encounter status page is a render choice); patching each route file;
`__root.tsx` (covers public and patient areas).

### 3.2 `IhrStaffPage` (new, `src/components/ihr/IhrStaffPage.tsx`)
```tsx
export function IhrStaffPage({ children }: { children: ReactNode }) {
  // one primitive: stable between router store updates (as AreaChrome.tsx:47-58 does)
  const signature = useMatches({ select: leafSignature });       // routeGates.ts, pure
  const leaf = useMemo(() => parseLeaf(signature), [signature]);  // §3.2 Leaf
  return (
    <IhrGrantWatcher scope={watcherScope(leaf.routeId)} facilityId={leaf.facilityId}>
      <IhrRouteGate leaf={leaf}>{children}</IhrRouteGate>
    </IhrGrantWatcher>
  );
}
```
- `leafSignature(matches)` (pure, `routeGates.ts`): let `ok` = the matches before the first one
  whose `status` is `"notFound"` or `"error"` (the same cut `layoutFlags.ts:50-54` makes); `d` =
  the last of `ok` (or `undefined`). Returns `JSON.stringify([d?.routeId ?? "__root__",
  d?.params ?? {}, d?.pathname ?? "/", ok.length < matches.length])`.
- `Leaf = { routeId, params, facilityId, failed }`, with
  `facilityId = params.facilityId ?? facilityFromPath(pathname)`; `facilityFromPath` is the rule
  of `useCurrentFacility.tsx:12-20` (segment 2 after `/facility/`), reimplemented pure in
  `routeGates.ts`. For core ids the two agree; the fallback covers a plugin route whose param has
  another name.
- **Failed matches (attack S4, D5, L2):** when any match failed, the gate is the gate of the
  **deepest match that did not fail** (`d` above), never a pass-through. Today no layout route
  with a component exists under the patient or encounter prefixes, so a failed child resolves to
  an `open` ancestor (`/_app`, or a list layout) and the not-found or error page shows; if
  upstream adds such a layout, the prefix rule gates it. Row in §3.4.
- The watcher is **always** rendered (scope `null` = inert), so one watcher instance lives across
  every staff navigation.
- `IhrRouteGate` is **one** component type whatever the gate, and returns `children` at the same
  position whenever the outcome is `care`. So CARE's "keep `PatientProfile` mounted across tabs"
  (`facility.$facilityId.patient.$patientId.{-$tab}.tsx` doc comment) and the encounters layout
  (`facility.$facilityId.encounters.tsx:9-14`) still hold.

### 3.3 Classification: prefix rules on route ids (`routeGates.ts`, pure, no table file)
`routeGateOf(id: string): RouteGate` keeps today's shape (`routeKeys.ts:95-109`). Prefixes are
regular expressions that accept **any** segment where upstream has a param, so an organization's
literal `organizationId` segment and plugin ids with other param names match too:

```ts
const FAC = String.raw`^/_app/facility/[^/]+`;
const ORG = String.raw`^/_app/organization/[^/]+`;
export const PATIENT_PREFIX_RE = new RegExp(`(${FAC}|${ORG})/patient/`);
export const ENCOUNTER_PREFIX_RE = new RegExp(`(${FAC}|${ORG})/patient/[^/]+/encounter/[^/]+/`);
```

Rules, first match wins:

| # | id | gate |
|---|---|---|
| 1 | `ENCOUNTER_ID` = `/_app/facility/$facilityId/patient/$patientId/encounter/$encounterId/$tab` | `encounter` |
| 2 | matches `ENCOUNTER_PREFIX_RE` (facility or organization; includes `ORG_ENCOUNTER_ID` = `/_app/organization/$organizationId/patient/$patientId/encounter/$encounterId/$tab`) | `encounterPage` |
| 3 | `BED_BOARD_TAB_ID` = `/_app/facility/$facilityId/encounters/$tab` | `bedBoardTab` |
| 4 | `BED_BOARD_LOCATION_ID` = `/_app/facility/$facilityId/encounters/locations/$locationId` | `bedBoardLocation` |
| 5 | `ENCOUNTER_LIST_IDS`: `.../encounters/patients/all`, `.../encounters/patients/$encounterClass`, and the redirect-only `.../encounters`, `.../encounters/`, `.../encounters/patients/` | `encounterList` |
| 6 | `PATIENT_TAB_ID` = `/_app/facility/$facilityId/patient/$patientId/{-$tab}` | `patientTab` |
| 7 | `DEMOGRAPHICS_IDS` (reviewed allow-list, facility only): `PATIENT_SEARCH_ID` `/_app/facility/$facilityId/patients/`, `PATIENT_HOME_ID` `.../patients/home`, `PATIENT_CREATE_ID` `.../patient/create`, `PATIENT_UPDATE_ID` `.../patient/$patientId/update`, `APPOINTMENT_ID` `.../patient/$patientId/appointments/$appointmentId/` | `patient` |
| 8 | matches `PATIENT_PREFIX_RE` (facility or organization) | `patientClinical` |
| 9 | anything else (including ids we do not know: plugin routes attached at run time) | `open` |

Mapping from today's keys: `ENCOUNTER_KEY` → `ENCOUNTER_ID` (param `id` → `facilityId`);
`ENCOUNTER_PAGE_PREFIX` → rule 2 (10 facility ids besides `ENCOUNTER_ID` at the tip);
`ENCOUNTER_QUESTIONNAIRE_KEY` → `.../encounter/$encounterId/questionnaire/` (rule 2);
`PATIENT_KEY` + the four `PATIENT_TAB_PREFIX + tab` keys → one `PATIENT_TAB_ID` (optional tab);
`FACILITY_PATIENT_PREFIX` → rule 8. `DEMOGRAPHICS_TABS` (`demography, appointments, encounters,
accounts`) stays exported (imported by `frontDesk.ts:16`). `/patients/` and `/patients/home` are
not under `/patient/`, which is why they are named (rule 7) as today.

**Organization addresses (Q-K170 B):** the same rules 2 and 8 apply under
`/_app/organization/<any>/patient/`. At the tip there is no organization counterpart of any
`DEMOGRAPHICS_IDS` id or of `PATIENT_TAB_ID`, so no organization demographics allow-list exists
(test §3.7.6 fails if upstream adds one, so the owner can decide whether it needs a facility-less
demographics view). The organization ids at the tip (all gated):
`.../patient/$patientId/encounter/$encounterId/$tab`,
`.../encounter/$encounterId/consents/$consentId` (literal `organizationId` segment),
`.../encounter/$encounterId/questionnaire_response/$questionnaireResponseId/print`,
`.../encounter/$encounterId/questionnaire/$questionnaireId/responses/print` (rule 2);
`.../patient/$patientId/diagnostic_reports/$diagnosticReportId/` and `/print`,
`.../questionnaire_response/$questionnaireResponseId/print`,
`.../questionnaire/$questionnaireId/responses/print`,
`.../service_request/$serviceRequestId/diagnostic_reports/print` (rule 8).

**`OPEN_CLINICAL_IDS` (reviewed, exported, explicit ids):** routes outside the gated prefixes that
show patient or visit data and stay `open` (Q-K170: backend refuses or projects for restricted
roles, menus hide them; a role-by-role walk-through is backlog). At the tip, the complete list:

| id | what it shows |
|---|---|
| `/patient/$id/{-$tab}` | facility-less staff chart (root-level shared) |
| `/patient/home` | root-level shared patient home (patient portal; staff see it via `staffOrPatient`) |
| `/referrals/$referralId/` | referral detail |
| `/referrals/$referralId/respond` | referral response |
| `/_app/patient/$patientId/history/$tab` | facility-less history |
| `/_app/patient/$patientId/history/questionnaire/$questionnaireId/responses/print` | print |
| `/_app/patient/$patientId/history/questionnaire_response/$questionnaireResponseId/print` | print |
| `/_app/patient/$patientId/questionnaire/` | facility-less questionnaire |
| `/_app/patient/$patientId/questionnaire/$questionnaireId/` | facility-less questionnaire |
| `/_app/patient/$patientId/questionnaire/$questionnaireId/responses/print` | print |
| `/_app/patient/$patientId/questionnaire_response/$questionnaireResponseId/print` | print |
| `/_app/facility/$facilityId/service_requests/$serviceRequestId` | service request detail |
| `/_app/facility/$facilityId/locations/$locationId/service_requests/` | location service request list |
| `/_app/facility/$facilityId/locations/$locationId/service_requests/$serviceRequestId` | service request detail |
| `/_app/facility/$facilityId/medication_dispense/redirect/$medicationDispenseId` | dispense redirect |
| `/_app/facility/$facilityId/locations/$locationId/medication_dispense/` | dispense list |
| `/_app/facility/$facilityId/locations/$locationId/medication_dispense/order/$dispenseOrderId/` | dispense order |
| `/_app/facility/$facilityId/locations/$locationId/medication_dispense/order/$dispenseOrderId/completed` | dispense order |
| `/_app/facility/$facilityId/locations/$locationId/medication_dispense/order/$dispenseOrderId/print` | print |
| `/_app/facility/$facilityId/locations/$locationId/medication_requests/patient/$patientId/prescriptions/$prescriptionIds/` | pharmacy patient page |
| `/_app/facility/$facilityId/locations/$locationId/medication_requests/patient/$patientId/prescriptions/$prescriptionIds/print` | print |
| `/_app/facility/$facilityId/locations/$locationId/medication_requests/patient/$patientId/bill/prescriptions/$prescriptionIds` | pharmacy bill |
| `/_app/facility/$facilityId/locations/$locationId/medication_requests/patient/$patientId/bill/dispense/$encounterId` | pharmacy bill |
| `/_app/facility/$facilityId/queue/$queueId/token/$tokenId` | queue token |

(24 ids: exactly the tip ids that match the §3.7.4 pattern and no gate rule.) Lists that do not
match the pattern (queues, appointments, referral lists, `/_app/organization/<any>/patients`,
`/_app/responsibilities/$id/patients`) stay `open` by default, as today.

### 3.4 Outcomes (pure `gateOutcome`, `routeGates.ts`) and `IhrRouteGate`
`gateOutcome(input) → "loading" | "notForRole" | "status" | "care"`, with input
`{ gate, tab, leafFacilityId, currentFacilityId, patientId, access, view, restrictedAnywhere,
isSuperuser }`. It reuses today's `patientGateOutcome`, `encounterGateOutcome`, `bedBoardOutcome`,
`encounterListOutcome` unchanged. Rows, first match wins:

| # | condition | outcome |
|---|---|---|
| 1 | gate ≠ `open` and `leafFacilityId !== currentFacilityId` (the address bar already names another facility, §1.4; attack S1) | `loading` |
| 2 | gate `open` | `care` |
| 3 | patient-family gate (`patient`, `patientTab`, `patientClinical`, `encounter`, `encounterPage`) and **no** `leafFacilityId` (organization addresses, Q-K170 B) | superuser → `care`; `restrictedAnywhere` unknown → `loading`; `true` → `notForRole`; `false` → `care` |
| 4 | `patientClinical`, `encounterPage`, `encounter` or `patientTab` inside a facility, the leaf has no `patientId` param (a plugin route under the prefix with another param name), and `access.mode === "restricted"` | `notForRole` |
| 5 | `encounter` | `encounterGateOutcome(view)` (demographics → `status`) |
| 6 | `encounterPage`, `patientClinical` | `patientGateOutcome(view, false)` |
| 7 | `patient` | `patientGateOutcome(view, true)` |
| 8 | `patientTab` | `patientGateOutcome(view, tab === undefined \|\| DEMOGRAPHICS_TABS.includes(tab))` |
| 9 | `bedBoardTab` | `tab === "locations"` ? `bedBoardOutcome(access)` : `care` |
| 10 | `bedBoardLocation` | `bedBoardOutcome(access)` |
| 11 | `encounterList` | `encounterListOutcome(access)` |

The failed-match rule (§3.2) is applied before: `gate` is already the gate of the deepest match
that did not fail. Rows 3 and 4 cannot occur at a run-time-only id unless that id falls under a
gated prefix (§3.5).

Why row 3 answers `notForRole` for every restricted person (also MRD with a grant, and also on
the organization encounter `$tab` id, the counterpart of `ENCOUNTER_ID`): the demographics view,
the encounter status page (`IhrEncounterStatus` needs `facilityId`, `IhrEncounterStatus.tsx:30-31`)
and records grants are facility-bound; outside a facility there is no answer to give. They open
the same page through its facility address. `restricted` from `GET /api/ihr/my-access/` without a
facility is the backend's own per-person rule (`ihr_access/endpoints.py:154-205`,
`state.for_user(user).restricted`), so it is the right facility-less answer.

**Facility from the leaf only (attack S1, readiness M2):**
- `useIhrAccess(opts?: { facilityId: string | undefined })`: with `opts`, `opts.facilityId` (even
  when it is `undefined`, i.e. no facility) is used instead of `useCurrentFacilitySilently()` for
  both the my-access query key/params and the facility query. An options object, not a bare
  optional argument, so "no facility" and "use the location" stay distinct. No `opts` = today's
  behaviour (CARE screens: `AccountShow`, `EncounterList`, `PatientHome`, overview, … keep it).
- The facility's permissions come from CARE's facility query with the same key
  (`["facility", facilityId]`, `useCurrentFacility.tsx:34-41`), called with the explicit id.
- `useIhrPatientView(patientId, opts?: { facilityId: string | undefined })` passes `opts` to
  `useIhrAccess` and uses the same facility in its patient query.
- `IhrRouteGate` calls, unconditionally: `useIhrAccess({ facilityId: leaf.facilityId })`,
  `useIhrPatientView(needsPatient ? leaf.params.patientId : undefined, { facilityId:
  leaf.facilityId })`, `useChartUnderGrant(...)` (moved from `IhrGates.tsx`), and
  `useCurrentFacilitySilently().facilityId` (only to compare, row 1), and
  `useRestrictedAnywhere(enabled)` with `enabled = isPatientFamily(gate) && !leaf.facilityId &&
  !user.is_superuser`.
- Renders: `loading` → `<Loading/>`; `notForRole` → `<IhrNotForRole facilityId={leaf.facilityId}/>`
  (already optional, `IhrNotForRole.tsx:11`); `status` → `<IhrEncounterStatus .../>` with the
  leaf's ids; `care` → `children`.
- Row 1 replaces the page with `<Loading/>` only on a cross-facility move inside the same route
  id (tab changes stay in one facility and keep the page mounted).

**`useRestrictedAnywhere(enabled)`** (extracted from `IhrGrantWatcher.tsx:223-228`, now in
`useIhrAccess.ts`): query key `["ihr", "my-access", undefined]`, `silent: true`, `staleTime` 1 min,
**`retry: 3` and `refetchInterval: (q) => q.state.status === "error" && q.state.data ===
undefined ? 15_000 : false`** (as `useIhrAccess.ts:43-45`; attack S5), returns `boolean |
undefined`. Shared by the gate (row 3) and the watcher (facility-less scope).

### 3.5 Unknown ids, and what is new vs parity
- **Unknown ids (plugin routes, a new upstream id): parity.** `routeGateOf` applies the prefix
  rules; an id under a gated prefix gets that gate, otherwise `open`. There is no separate
  "unknown" branch any more (attack M2).
- **Plugin pages for restricted roles are out of scope for P2-T0c:** none exist; the backend is
  the control for them (attack D4).
- **New behaviour in P2-T0c** (everything else is parity with the raviger integration):
  1. Organization patient and encounter pages are gated (Q-K170 B; row 3).
  2. Cross-facility `loading` (row 1) and the facility taken from the leaf.
  3. Failed matches use the deepest non-failed match's gate (§3.2).
  4. A gated id without `patientId` fails closed for restricted users (row 4).
  5. CI invariant on clinical ids (§3.7.4) and the reviewed `_public` list (§4.4).
- **Parity:** facility patient and encounter gates, demographics allow-list, patient-tab rule, bed
  board, encounter lists, `open` default, facility-less charts open (`OPEN_CLINICAL_IDS`), grant
  watcher scopes.

### 3.6 Grant watcher (`watcherScope(routeId)`)
- `"facility"`: id matches `^/_app/facility/[^/]+/` (includes our pages).
- `"facilityLess"`: `^/_app/patient/`, `^/patient/\$id/`, or `^/_app/organization/[^/]+/patient/`.
- `null` otherwise (inert watcher). Parity with `routeKeys.ts:113-124`.
`IhrGrantWatcher` props: `{ scope: "facility" | "facilityLess" | null; facilityId: string |
undefined; children }`. It calls `useIhrAccess({ facilityId })` (not the location), uses
`useRestrictedAnywhere(scope === "facilityLess" && !facilityId && !user.is_superuser)`, and keeps
`facilityRef` in step with the **prop**. `enabled` is false when `scope === null`. Its `act()`
navigation becomes `navigate({ href: facility ? `/facility/${facility}/ihr/records` : "/",
replace: true })`.
There is no run-time self-check (`useRequiredRoutesCheck` dropped, attack M3): the unit test
(§3.7.2), the checker anchors (§6.3) and the build-time imports (§4.1) cover the same ground.

### 3.7 Tests (`routeGates.test.ts`, `node --test`, replaces `routeKeys.test.ts`)
**How data loads (readiness M3):** `routeGates.ts` is pure: no React, no router import, no JSON
import, no `@/` alias (relative imports only, e.g. `import type { IhrAccess, IhrPatientView } from
"./access"`, as `routeKeys.ts:8` does). The test reads the tree as text, like the existing tests
read JSON (`routeKeys.test.ts:30-33`): `readFileSync(join(process.cwd(), "src",
"routeTree.gen.ts"), "utf-8")`, takes the `export interface FileRoutesById { ... }` block and
collects every `"<id>": typeof` key plus `__root__` (`treeIds(text)`, exported from
`routeGates.ts` so the checker logic is testable). CI runs from `apps/frontend`.

1. **Parser sanity:** at least 300 ids, includes `__root__`, `/_app`, `ENCOUNTER_ID`.
2. **Named ids exist:** every exported `*_ID`, every id in `DEMOGRAPHICS_IDS`,
   `ENCOUNTER_LIST_IDS`, `OPEN_CLINICAL_IDS` and `PUBLIC_ROUTES` (§4.4) is a key of
   `FileRoutesById` (a renamed upstream id fails).
3. **Gated prefixes never open:** every tree id matching `PATIENT_PREFIX_RE` (facility **and**
   organization) has a gate other than `open`; every id matching `ENCOUNTER_PREFIX_RE` except
   `ENCOUNTER_ID` is `encounterPage`; every facility patient-prefix id that is not in
   `DEMOGRAPHICS_IDS`, not `PATIENT_TAB_ID` and not under the encounter prefix is
   `patientClinical`; `DEMOGRAPHICS_IDS` has exactly the five ids of §3.3 row 7.
4. **Clinical invariant (attack S2, D1):** pure `clinicalProblems(ids, gateOf, openClinical)`
   returns one problem per id that matches `CLINICAL_ID_RE` =
   `/\$patientId|\$encounterId|\/patient\/|service_request|medication_dispense|\/token\/|\/referrals\//`,
   has gate `open` and is not in `OPEN_CLINICAL_IDS`. On the real tree: no problem, and
   `OPEN_CLINICAL_IDS` equals exactly the set of open ids matching the pattern (a stale list entry
   fails too). **Mutation cases** (each expects one problem): synthetic ids
   `/_app/facility/$facilityId/visit/$encounterId`, `/_app/facility/$facilityId/x/$patientId`,
   `/_app/facility/$facilityId/x/patient/y`, `/_app/facility/$facilityId/service_requests/x/new`,
   `/_app/facility/$facilityId/medication_dispense/x`, `/_app/facility/$facilityId/queue/$q/token/x`,
   `/referrals/$referralId/x` (one per pattern element); and the real tree with one id removed
   from `OPEN_CLINICAL_IDS`. A synthetic `/_app/organization/organizationId/patient/$patientId/x`
   must get `patientClinical` (any-segment rule).
5. **Public routes:** the set of tree ids starting `/_public/` equals `PUBLIC_ROUTES` keys (§4.4).
6. **Organization counterparts:** for each id in `DEMOGRAPHICS_IDS` and `PATIENT_TAB_ID`, its
   organization form (prefix `/_app/facility/$facilityId` replaced by
   `/_app/organization/$organizationId`) is absent from the tree.
7. **Params:** every id whose gate reads `patientId`, `encounterId` or `tab` contains
   `$patientId`, `$encounterId`, `$tab`/`{-$tab}` (catches a renamed param), except under the
   organization prefix (row 3 needs no patient).
8. **Outcomes:** every row of §3.4 × access mode (pending, restricted demographics, restricted
   none, records with/without grant view, care, superuser) × tab, including: row 1 (facility A
   leaf, facility B current → `loading` for each non-open gate; `open` → `care`); row 3 for
   `ORG_ENCOUNTER_ID` and an organization `patientClinical` id (restricted → `notForRole`, unknown
   → `loading`, not restricted → `care`, superuser → `care`); row 4; and the failed-match case
   (`leafSignature` over matches `[/_app ok, ENCOUNTER_ID notFound]` gives `/_app`, `failed:
   true`; over `[/_app ok, a synthetic patient layout ok, child notFound]` gives the layout and
   its `patientClinical` gate → restricted `notForRole`).
9. **Watcher scope:** every `/_app/facility/<seg>/**` id → `facility`; every `/_app/patient/**`,
   `/patient/$id/**`, `/_app/organization/<seg>/patient/**` id → `facilityLess`; others `null`.
10. **Route-tree merge (§4.1):** `mergeChildren(existing, ours, idOf)` called twice gives the same
    list (no duplicate id), and replaces the three auth ids in place.

`frontDesk.test.ts:42` imports `APPOINTMENT_KEY, routeGate` today: switch to `APPOINTMENT_ID`,
`routeGateOf`.

## 4. Our own pages and the auth redirects

### 4.1 Code-based routes (chosen)
New `src/components/ihr/ihrRouteTree.tsx` (React, imported only by `router.tsx`) and the pure
helper `mergeChildren` in `routeGates.ts`:

| page id (`/_app` + path) | path | component |
|---|---|---|
| `/_app/facility/$facilityId/ihr/audit` | `/facility/$facilityId/ihr/audit` | `IhrAuditTrail` |
| `/_app/facility/$facilityId/ihr/packs` | `/facility/$facilityId/ihr/packs` | `IhrPackUpdates` |
| `/_app/facility/$facilityId/ihr/places` | `/facility/$facilityId/ihr/places` | `IhrMyPlaces` |
| `/_app/facility/$facilityId/ihr/records` | `/facility/$facilityId/ihr/records` | `IhrRecordsAccess` |
| `/_app/facility/$facilityId/ihr/totals` | `/facility/$facilityId/ihr/totals` | `IhrOwnerTotals` |

(Today's table: `IhrRoutes.tsx:112-128`; each takes `facilityId` as a prop.) The ids are exported
as `IHR_PAGE_IDS` from `routeGates.ts` (pure) and get gate `open` (rule 9; the pages gate
themselves) and watcher scope `facility`.

```tsx
import { type AnyRoute, createRoute, redirect, useParams } from "@tanstack/react-router";
import { Route as AppRoute } from "@/routes/_app";
import { Route as PublicRoute } from "@/routes/_public";
import { Route as TwoFaRoute } from "@/routes/_public/2fa";
import { Route as ForgotRoute } from "@/routes/_public/forgot-password";
import { Route as ResetRoute } from "@/routes/_public/password_reset.$token";
import { IHR_AUTH_REDIRECT_IDS, mergeChildren } from "@/Utils/ihr/routeGates";

function IhrPageRoute({ Page }: { Page: ComponentType<{ facilityId: string }> }) {
  const { facilityId } = useParams({ strict: false });
  return <Page facilityId={facilityId!} />;
}

const IHR_PAGES = [
  { path: "/facility/$facilityId/ihr/audit", Page: IhrAuditTrail },
  /* packs, places, records, totals as in the table */
] as const;

export function withIhrRouteTree<T>(routeTree: T): T {
  const ours = IHR_PAGES.map(({ path, Page }) => createRoute({
    getParentRoute: () => AppRoute, path, component: function IhrPage() { return <IhrPageRoute Page={Page} />; },
  }));
  AppRoute.addChildren(mergeChildren(AppRoute.children as AnyRoute[], ours, (r) => r.id));
  const toLogin = [TwoFaRoute, ForgotRoute, ResetRoute].map((r) => createRoute({
    getParentRoute: () => PublicRoute, path: r.path,
    beforeLoad: () => { throw redirect({ to: "/login", replace: true }); },
  }));
  PublicRoute.addChildren(mergeChildren(PublicRoute.children as AnyRoute[], toLogin, (r) => r.id));
  return routeTree;
}
```
- Hooks live only in the named `IhrPageRoute` (no hook in an inline arrow; attack M4). The
  per-page `component` is a named function so rules-of-hooks and React devtools are clean.
- **Idempotent (attack D2, M4):** `mergeChildren(existing, ours, idOf)` keeps `existing` order,
  replaces an existing child whose id is in `ours` in place (the three auth routes), and appends
  the others only if their id is not already present. So a second call (Vite HMR re-evaluating
  `router.tsx`, or a test) adds nothing. Tested in §3.7.10.
- Patch `src/router.tsx`: one marked import and `routeTree,` → `routeTree: withIhrRouteTree(routeTree),`.
- It runs before `createRouter` builds the tree, so there is no flash of the `_app/$` splat and no
  `router.setRoutes`. `PluginRouteAttacher` keeps our routes as base children (taken on first
  attach) and refuses plugins that shadow them (`pluginRoutes.tsx:140-145`).
- The imported route modules make a renamed or removed upstream file a **build/type error**.
- **Sidebar:** our pages set no `staticData`; the defaults (`layoutFlags.ts:42`) and the
  `/facility/:facilityId` prefix match (`app-sidebar.tsx:65-86`) give them the facility nav, as
  CARE's own facility pages that are direct children of `/_app` (§1.3). Proof: a Playwright
  assertion in `records.spec.ts` that `/facility/<id>/ihr/records` shows the facility nav (the
  same sidebar locator `roles-menus.spec.ts` uses for a facility page).

### 4.2 Builder check after `npm ci` (which redirect form TanStack accepts)
Read `node_modules/@tanstack/router-core/dist/esm/route.js` (source `src/route.ts`):
- `BaseRoute.addChildren` must replace `children` (not append) and accept code routes beside file
  routes; the redirect routes must get the same id (`/_public/2fa`, `/_public/forgot-password`,
  `/_public/password_reset/$token`) so typed links elsewhere still resolve.
- **Preferred:** the code redirect routes above (`beforeLoad` throws `redirect`), which also stop
  CARE's `Authenticate`, `ForgotPassword` and `ResetPassword` from rendering at all (they stay
  bundled: other upstream modules import them).
- **Fallback** if replacing a file route with a code route of the same id is refused (duplicate id
  error or type error that cannot be fixed with a cast on `AnyRoute`): keep the file routes and
  call `r.update({ component: ToLogin })` with `function ToLogin() { return <Navigate to="/login"
  replace />; }`, after confirming `update()` does `Object.assign(this.options, options)` and that
  `component` is in `UpdatableRouteOptions`.
- If neither works, use five file routes `src/routes/_app/facility.$facilityId.ihr.<page>.tsx`
  for our pages (which makes `routeTree.gen.ts` differ from upstream's and conflict on every sync
  that touches routes): stop and report before doing that.

### 4.3 Redirect behaviour
`publicOnly` on `/_public` runs first, so a signed-in staff user visiting `/2fa` goes to `/`; a
signed-out visitor goes to `/login` (any query is dropped, which is intended).

### 4.4 Reviewed public routes (`PUBLIC_ROUTES`, `routeGates.ts`; attack S6)
Every `/_public/**` id with its mark. A new `_public` id fails §3.7.5 until listed here.

| id | mark |
|---|---|
| `/_public` | layout: `publicOnly` guard only, no component |
| `/_public/login` | our patched `Login.tsx` (§5.3): staff panel = `IhrStaffSignIn`, no password form |
| `/_public/2fa` | `ToLogin` (§4.1) |
| `/_public/forgot-password` | `ToLogin` |
| `/_public/password_reset/$token` | `ToLogin` |
| `/_public/invalid-reset` | harmless: static `ErrorPages/InvalidReset`, no form (builder confirms after the merge; its link goes to `/forgot-password`, which redirects) |
| `/_public/facilities` | harmless: public facility finder; backend trims it (`public_routes.py`, P1-T20a) |
| `/_public/facility/$id` | harmless: public facility page; same trimmed backend |
| `/_public/facility/$facilityId/appointments/$staffId/otp/$page` | patient portal OTP sign-in (§5.4), not a staff credential |

The after-sync checklist in `docs/UPSTREAM.md` gets the line "new `_public` route: is it an auth
flow (password, 2FA, invite, set-password)? If so add it to the `ToLogin` routes; then list it in
`PUBLIC_ROUTES`" (written by the builder, §7.5).

## 5. Sign-in and session on the new router

### 5.1 `AuthUserProvider.tsx` (re-port onto upstream's file)
Keep upstream's structure (no props; `setAuthStatusResolver`; router `invalidate()` on status
change, `useLayoutEffect`, upstream 129-137; ref mirrors for synchronous reads, 93-111;
`isUserLoading` in the context, 358). Replace upstream's token logic with ours (today's file):
- `["currentUser"]` query, always enabled unless `signOutPending()`; no `accessToken`, no refresh
  query, no `authApi.login`/`mfa` mutations, no `authApi.logout`.
- `signIn` → `startSignIn(redirect or current path)`; `verifyMFA` → `navigate({ href: "/login" })`
  (both return never-settling promises, as today).
- `endLocally`, `endSession(idle, clearDrafts)`, `signOut`, `endSessionKeepingDrafts`,
  `onSignOutElsewhere`, `useIdleSignOut`, `forgetLegacyTokens`, `setStaffSignedIn`, the fill-draft
  sweep and `clearOtherUsersFillDrafts(user.id)`: as today. Sign-out stays a full page load
  (`window.location.replace("/login?reason=...")`), never a router navigation.
- `patientLogin(tokenData, url)`: set state **and** ref, `navigate({ href: url })`. Patient token
  removed in another tab: set state and ref to `null`; the status change re-runs guards.
- Render `children` always (the root route shows `<Loading/>` until the area is known).

New pure `src/Utils/ihr/authStatus.ts`, a **thin function over the provider's existing state**
(not a second state machine; attack L2):
`ihrAuthStatus({ pendingSignOut, user: { status, hasData } | undefined, patientToken }) →
AuthStatus`: pending sign-out → patient/anonymous; data → `user`; no state or `pending` →
`loading`; else `patient` (token) or `anonymous`. The resolver reads
`queryClient.getQueryState(["currentUser"])` and the refs; the rendered context derives
`user`/`isUserLoading` so that `AreaChrome.useRenderedAuthStatus` (29-34) yields the same value.
`authStatus.test.ts` covers every row, including "a refetch error keeps data → user".

### 5.2 Redirects and the `redirect` / `next` parameters
- Signed out on a staff URL: `requireStaff` → `/login?redirect=<absolute URL>` (auth.ts:81-87).
  `IhrStaffSignIn` passes `params.redirect` to `startSignIn` → `safeNext` (`idleRules.ts:55-67`).
  New `idleRules.test.ts` rows with origin `http://alpha.localhost:8080` (expected outputs):

  | input | `safeNext` returns |
  |---|---|
  | `http://alpha.localhost:8080/facility/x/patients?page=2#top` | `/facility/x/patients?page=2` (the hash is dropped) |
  | `https://evil.test/x` | `/` |
  | `//evil.test` | `/` |
  | `/\evil.test` | `/` |
  | `javascript:alert(1)` | `/` |

- `endExpiredSession` keeps `/login?reason=session_ended&redirect=<path+query>` (relative): fine
  for `safeNext`. `AuthUserProvider.getRedirectOr` (`new URL(url)`, absolute only) is replaced by
  `safeNext`.
- `/login` for a signed-in staff user: `publicOnly` sends them to `/`.
- `/session-expired` (new shared route): reached only from `errorHandler.handleSessionExpired`
  when no staff person is signed in here; staff go through `endExpiredSession` first.
- **Round-trip proof (readiness M5):** new case in `tests-ihr/preflight.spec.ts`, "a signed-out
  staff URL returns to itself after sign-in": a fresh context visits
  `<alpha>/facility/<alpha facility id>/patients` (the id the specs already use), asserts the
  page lands on `/login` with `redirect` = that **absolute** URL, then signs in through Keycloak
  with the steps of `lib/fixtures.ts` `signIn` (lines 128-158: password, then a fresh one-time
  code; extend `signIn` with an optional `{ from: string }` that starts at that URL instead of
  `/login`), and asserts the final URL's path and query equal the original. Keycloak runs in CI
  already (the Playwright job), so this is a CI proof, not a manual one.

### 5.3 Login page
Take upstream's `Login.tsx` (`useQueryParams(loginPageSearch)`, `mode` = staff | patient,
`forgot` prop) and re-apply: staff panel = `<AuthCard title=staff_sign_in><IhrStaffSignIn/></AuthCard>`
(no `StaffLoginForm`, no `ForgotPassword`, `forgot` ignored). `IhrStaffSignIn` reads
`error`, `reason`, `redirect` with `useQueryParams()` (raw strings; `queryParams.ts:68,85` read
`searchStr`).

### 5.4 Patient portal (OTP)
Routes: `_public/facility.$facilityId.appointments.$staffId.otp.$page.tsx`, `_patient/**`
(`requirePatient`). Re-apply: `usePatientSignOut.ts` also removes `user` (P1-T1d) onto upstream's
`navigate({ href })` version; `pages/PublicAppointments/PatientRegistration.tsx` PIN prop.
`clearStaffStorage()` still removes `care_patient_token`.

## 6. Watcher, menus, anchors, storage, batch, dates

### 6.1 Watcher
§3.6. `records.spec.ts` (Playwright) still proves expiry/end navigation.

### 6.2 Menus
Unchanged (table in §2). `check-ihr-frontend.py` §1 still reads `facility-nav.tsx` and
`location-nav.tsx`.

### 6.3 `scripts/check-ihr-frontend.py` (and `scripts/test_check_ihr_frontend.py`)
Remove `CONSULTATION_ROUTES`, `PATIENT_ROUTES`, `SCHEDULE_ROUTES`, `APP_ROUTER`, `IHR_ROUTES`,
`ROUTE_KEYS`, `KEY_FILES`, `ENCOUNTER_UPSTREAM`, `TAB_PREFIX`. Add:
- `ROUTER = src/router.tsx`: marker + `withIhrRouteTree(routeTree)`.
- `APP_SHELL = src/Routers/AppShell.tsx`: marker + `<IhrStaffPage>` and `</IhrStaffPage>` with
  `{children}` between them, inside `<ErrorBoundary`.
- Area guards (unmarked upstream text we rely on): `routes/_app.tsx` `beforeLoad: requireStaff`;
  `routes/_public.tsx` `beforeLoad: publicOnly`; `routes/_patient.tsx` `beforeLoad: requirePatient`;
  `routes/__root.tsx` `<AreaChrome`; `Routers/AreaChrome.tsx` `case "app":` followed by `<AppShell>`
  and `isAreaAllowed(`; `lib/router/auth.ts` `if (area === "app") return status === "user";`.
- Location rule we rely on (§1.4): `pages/Facility/utils/useCurrentFacility.tsx` contains
  `usePathname()` and `segments[1] === "facility"` (if upstream changes how the current facility
  is found, row 1 must be re-checked).
- `ROUTE_TREE = src/routeTree.gen.ts`: every `export const \w+_ID = "..."` of `routeGates.ts` is a
  key of `FileRoutesById` (fast local feedback in pre-commit; the unit test is the full check).
- `IHR_STAFF_PAGE`: contains `<IhrGrantWatcher` enclosing `<IhrRouteGate`, and passes
  `facilityId={leaf.facilityId}`.
- **No data fetches before the gate (attack S3):** in `src/routes/**` and
  `src/lib/router/pluginRoutes.tsx`, fail on `\bloader(Deps)?\b` and on
  `queryClient|ensureQueryData|prefetch(Infinite)?Query|fetchQuery`. Both are absent at the tip
  (§1.1). Any hit needs a review (a loader or a `beforeLoad` fetch runs on hover preload and on
  navigation, before our render-time gate). Plugin loaders attached at run time are not covered
  by this text check (attack D6); plugins for restricted roles are out of scope (§3.5).
- Keep `PATIENT_TABS` (`components/Patient/PatientDetailsTab/index.tsx`): each `DEMOGRAPHICS_TABS`
  name is a tab route.
Docstring and `FILES` list updated. Tests: one fixture case per new anchor that removes it and
expects the named problem, and **one fixture case per fetch pattern** (`loader:`, `loader()`,
`loaderDeps`, `context.queryClient`, `ensureQueryData`, `prefetchQuery`,
`prefetchInfiniteQuery`, `fetchQuery`, in a route file and in `pluginRoutes.tsx`).

**Known gap (P2-T0c verifier, 2026-11-24):** the check reads route files only. A `beforeLoad` that calls a helper imported from outside `src/routes/**` (for example `beforeLoad: loadX` with `loadX` in `src/Utils` using `.then()`) is not caught. Today every `beforeLoad` is one of upstream's guard or redirect forms; review a new named `beforeLoad` at sync time (docs/UPSTREAM.md checklist).

### 6.4 Storage keys
- The router keeps scroll positions in **sessionStorage** (`scrollRestoration: true`,
  router.tsx:23); the key lives in `node_modules`, so `check-storage-keys.py`, which scans `src`,
  cannot see it. **After `npm ci`**, read
  `node_modules/@tanstack/router-core/dist/esm/scroll-restoration.js` (source
  `src/scroll-restoration.ts`): note `storageKey` and the default key function
  (`getScrollRestorationKey` / `defaultGetScrollRestorationKey`). If the stored keys can be a
  `location.href` (patient ids in paths; e.g. a fallback when `state.__TSR_key` is missing): add
  its removal to the start-up path when no staff session exists and classify it in
  `tests-ihr/storage-keys.json` (`content_checked.session`); `sign-out-cleanup.spec.ts` must pass.
  If only random per-navigation keys are stored, record that in the result and change nothing
  (attack D7).
- New upstream code adds no storage keys besides upstream's token keys (`care_access_token`,
  `care_refresh_token`, `mfa_temp_token`), which our provider never writes and `LEGACY_KEYS`
  clears. Run `python3 scripts/check-storage-keys.py`.

### 6.5 Batch allow-list
`python3 -I scripts/batch-allowlist.py --check` after the merge. Known: the reviewed DYNAMIC site
`components/Patient/LinkDepartmentsSheet.tsx` changed upstream, so its pinned sha256 fails until
re-reviewed (re-read the site, confirm the routes, update the hash). Regenerate the list if call
sites moved; review the diff of `plugins/ihr_platform/ihr_platform/batch_allowlist.txt`.

### 6.6 Dates, locale, egress
`python3 scripts/check-date-formats.py` and `python3 scripts/merge-locale.py --check` after the
merge. `scripts/check-bundle-egress.sh` runs in CI on the build: router devtools are
`import.meta.env.DEV`-only (`__root.tsx:16-22`); any new host goes to
`deploy/frontend-egress-allow.txt` only after review.

## 7. Merge plan

### 7.1 Order (three commits, nothing pushed until §7.4 passes; no hook is ever skipped)
Owner rule: never `--no-verify`, never `SKIP=`. The merge commit therefore carries the conflict
resolutions **and** the port, so every hook passes on it.
1. Clean tree; `git fetch upstream-frontend`; `scripts/sync-upstream.sh frontend --dry-run`
   (expected: about 161 commits, about 1,300 files, about 50 patched files flagged).
2. `IHR_COMMIT_TRAILER='Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'
   scripts/sync-upstream.sh frontend 91b028f3aa331e4c9e185b883fc3edc3db373019 --task P2-T0c`
   → expect **exit 2**: the script stops at "MERGE CONFLICT" and leaves the merge uncommitted
   (`MERGE_HEAD` set, `upstream.lock` untouched; `scripts/sync-upstream.sh:116-120`). (If it
   exits 0 instead, the merge is already committed: do the port in a follow-up commit and report
   the deviation.)
3. In that working tree: resolve the conflicts (§7.2), do the port (§3–§6, file list §7.3),
   `git add` everything, then run §7.4 steps 1–6 on the uncommitted merge.
4. **Commit A** (the merge commit itself): `git commit` with message "P2-T0c: merge upstream CARE
   web app 60736fa3d2cf..91b028f3aa33 (161 commits); port route gates, our pages and sign-in to
   TanStack Router" plus the attribution trailer. All hooks run. If a hook fails, fix the cause
   and commit again (the merge stays in progress until it succeeds).
5. **Commit B:** `upstream.lock` `frontend.sha`/`frontend.date` (runbook step 4,
   `docs/UPSTREAM.md` "Conflict runbook") and the `docs/UPSTREAM_PATCHES.md` rows (§7.5).
6. **Commit C** "P2-T0c: docs": the rest of §7.5.
7. §7.4 step 7; then push A–C together; Apps CI and Playwright must be green; record the CI run.

### 7.2 Conflict resolution (the flagged files)
| Group | Files | Resolution |
|---|---|---|
| Deleted upstream, patched by us | `src/Routers/AppRouter.tsx`, `src/Routers/PublicRouter.tsx` | `git rm`; logic moves (§3, §4) |
| Lock / generated | `package-lock.json` | `--theirs`; re-apply our security entries only where ours is newer (DOMPurify ≥ 3.4.17, the vite 8.3.5 set); drop rows upstream overtook |
| Text equal to upstream | `public/locale/en.json` | `--theirs` (our English lives in `deploy/locale/en.json`) |
| Sign-in / routing (port) | `Providers/AuthUserProvider.tsx`, `components/Auth/Login.tsx`, `Utils/request/errorHandler.ts`, `hooks/usePatientSignOut.ts` | upstream's file + §5 |
| Our hunk uses raviger | `pages/Facility/billing/account/AccountShow.tsx` (`navigate`), `pages/Facility/locations/LocationOverview.tsx` (`Link`) | upstream's file; re-apply our hunk with the file's new idiom |
| Build config | `vite.config.mts` (our `indiaDateFnsPlugin`), `index.html` (bootstrap.js), `care.config.ts`, `src/index.tsx` | upstream's file + our lines; keep `tanstackRouter(...)` before `react()` |
| Everything else (dates, money, PIN, front-desk hooks, nav-user, users, appearance, query headers, file upload) | the remaining files | upstream's change + our marked hunk per its `docs/UPSTREAM_PATCHES.md` row; drop a row where upstream now does the same |
Files that only lost raviger upstream and that we never patched merge cleanly. `src/App.tsx` is
not ours: take upstream's.

### 7.3 Port file list (part of commit A)
- **New (ours):** `src/components/ihr/IhrStaffPage.tsx`, `src/components/ihr/ihrRouteTree.tsx`,
  `src/Utils/ihr/routeGates.ts`, `routeGates.test.ts`, `src/Utils/ihr/authStatus.ts`,
  `authStatus.test.ts`.
- **Deleted (ours):** `src/components/ihr/IhrRoutes.tsx`, `src/Utils/ihr/routeKeys.ts`,
  `routeKeys.test.ts`.
- **Changed (ours):** `IhrGates.tsx` (→ `IhrRouteGate`), `IhrGrantWatcher.tsx` (scope,
  `facilityId` prop, navigate, `useRestrictedAnywhere`), `Utils/ihr/useIhrAccess.ts` (optional
  facility, `useRestrictedAnywhere`), `IhrNotForRole.tsx`, `IhrEncounterStatus.tsx`,
  `IhrMyPlaces.tsx`, `IhrPatientActions.tsx`, `IhrRecordsAccess.tsx`, `IhrAuditTrail.tsx`,
  `IhrOwnerTotals.tsx`, `IhrPackUpdates.tsx`, `components/Auth/IhrStaffSignIn.tsx`,
  `Utils/ihr/frontDesk.ts` (import), `frontDesk.test.ts`, `idleRules.test.ts` (§5.2 rows),
  possibly `staffSession.ts` (§6.4), `tests-ihr/lib/fixtures.ts` (`signIn` `from` option, drop
  `ihr-routes`), `tests-ihr/preflight.spec.ts` (round trip §5.2; `/2fa`, `/forgot-password`,
  `/password_reset/x` end on `/login`), `tests-ihr/records.spec.ts` (facility nav on our page),
  `tests-ihr/storage-keys.json` (§6.4 if needed).
- **Upstream files patched (new rows):** `src/router.tsx`, `src/Routers/AppShell.tsx`; rewritten
  rows: `AuthUserProvider.tsx`, `Login.tsx`, `errorHandler.ts`, `usePatientSignOut.ts`.
- **Scripts:** `scripts/check-ihr-frontend.py`, `scripts/test_check_ihr_frontend.py`,
  `scripts/batch-allowlist.py` (DYNAMIC hash). No CI workflow change (the `routeTree.gen.ts` diff
  step is dropped, attack M3).
- Search after the port: `grep -rn 'raviger' apps/frontend/src apps/frontend/tests-ihr scripts`
  → only historical comments (reword them).

### 7.4 Local proofs (6.7 GB RAM: no local build; stop the dev stack first)
From `apps/frontend` (quote the repo path):
1. `npm ci` (new deps: `@tanstack/react-router` 1.170.46, `@tanstack/router-plugin` 1.169.0).
   Then the two reads: §4.2 (`route.js`) and §6.4 (`scroll-restoration.js`).
2. `NODE_OPTIONS=--max-old-space-size=2200 npx tsc --noEmit` and `npx tsc -p tests-ihr/tsconfig.json`.
   Retry at 3000 with nothing else running; if it still fails for memory only, say so and rely on
   CI's typecheck (do not claim it passed).
3. `node --import tsx --test src/Utils/ihr/*.test.ts` (all pass; quote the counts).
From the repo root:
4. `python3 -m unittest scripts/test_check_ihr_frontend.py` and `python3 -I scripts/check-ihr-frontend.py`.
5. `python3 scripts/check-storage-keys.py`, `python3 -I scripts/batch-allowlist.py --check`,
   `python3 scripts/check-date-formats.py`, `python3 scripts/merge-locale.py --check`.
6. `pre-commit run --all-files` (before commit A).
7. After commit C: `scripts/sync-upstream.sh frontend --dry-run` → up to date; `graphify update .`.
8. Optional, memory permitting: `npx eslint src/components/ihr src/Utils/ihr src/components/Auth src/Providers`.
CI then proves: typecheck, lint, unit tests, build, bundle egress, image smoke/Trivy, Playwright
`tests-ihr/**` (roles-menus, front-desk, billing-desk, records, admin-audit, pack-updates,
owner-totals, idle, preflight, sign-out-cleanup, switcher, synthetic-banner, ...).

### 7.5 Docs (commits B and C)
B: `docs/UPSTREAM_PATCHES.md` (new rows for `router.tsx`, `AppShell.tsx`; rows for
`AppRouter.tsx` and `PublicRouter.tsx` marked superseded; rewritten rows for the four sign-in
files and the two hunks of §7.2; lock rows). C: `docs/UPSTREAM.md` after-sync checklist (replace
the `routeKeys.ts` bullet with: "new route ids: `routeGates.test.ts` fails if a new id with
patient or visit data is neither gated nor in `OPEN_CLINICAL_IDS`, or if a named id moved; a new
`_public` route: is it an auth flow? (§4.4); a new `loader`/`queryClient` use in routes fails
`check-ihr-frontend.py`"), `docs/VERSIONS.md` (frontend pin, router versions), `docs/TRACKER.md`
(including a backlog row: role-by-role review of `OPEN_CLINICAL_IDS`), `docs/QUESTIONS.md`
(Q-K170 is already answered; add only the implementation note), `CLAUDE.md` only if a line names
a deleted file.

## 8. Risks and how each is proven fail-closed

| # | Risk | Control | Proof (fails if the control is removed) |
|---|---|---|---|
| R1 | A future sync drops the `AppShell` hook or the `router.tsx` call | anchors §6.3 | `test_check_ihr_frontend.py` removal cases; Playwright `roles-menus.spec.ts` expects `IhrNotForRole` on typed addresses |
| R2 | New upstream route with patient or visit data reaches restricted roles | prefix rules; clinical invariant | §3.7.3, §3.7.4 (CI red until gated or listed) |
| R3 | Reviewer marks a clinical route `open` | invariant; exact `OPEN_CLINICAL_IDS` set | §3.7.4 mutation cases |
| R4 | Gate and page read different pages (race): route from the committed match, facility from the next location | facility only from the leaf (§3.4); row 1 `loading` on mismatch | §3.7.8 row-1 cases (the unit row is the proof). Playwright: if the dev seed has a person with a clinical role in one facility and a restricted one in another (check `ihr_seed_demo`), add to `front-desk.spec.ts` a same-route move between their two facilities' encounter pages and assert the status page never shows the clinical tabs; if not, record that the end-to-end case is not available with the seed |
| R5 | Router guard and rendered chrome disagree on the session | one pure `ihrAuthStatus` for both | `authStatus.test.ts`; idle/sign-out specs |
| R6 | Staff page renders before the user is known | `requireStaff` loading → root `<Loading/>`; `isAreaAllowed` | area anchors §6.3; `preflight.spec`/`token-isolation.spec` |
| R7 | `/2fa`, password reset render CARE's forms; a new upstream auth page ships CARE's form | redirect routes (§4.1); `PUBLIC_ROUTES` review | anchors §6.3; `preflight.spec.ts`: `/2fa`, `/forgot-password`, `/password_reset/x` end on `/login`; §3.7.5 |
| R8 | Open redirect through `redirect` | `safeNext` | `idleRules.test.ts` rows §5.2 |
| R9 | Watcher misses a route, remounts, or watches the wrong facility | always-mounted watcher, `watcherScope`, `facilityId` from the leaf | §3.7.9; `IHR_STAFF_PAGE` anchor; `records.spec.ts` |
| R10 | Scroll-restoration storage outlives sign-out | §6.4 | `sign-out-cleanup.spec.ts` |
| R11 | TanStack internals (`addChildren`, redirect replacement) change on a bump | build-time imports; §4.2 check; idempotent merge | Playwright opens all five `/ihr/*` pages (admin-audit, pack-updates, records, owner-totals, places in roles-menus); §3.7.10 |
| R12 | A route gains a data fetch that runs before our render-time gate (also on hover preload) | §6.3 fetch check | one checker fixture case per pattern |
| R13 | A failed child renders an ancestor layout's patient content | deepest non-failed match's gate (§3.2) | §3.7.8 failed-match cases |
| R14 | Organization address bypasses the patient/encounter gate | Q-K170 B rules 2 and 8 + row 3 | §3.7.3 (organization ids), §3.7.8 row 3 |
| R15 | A conflict resolution silently drops a marked hunk | existing hook anchors (`HOOKS`, `FRONT_DESK_ANCHORS`, ...) + typecheck | `check-ihr-frontend.py` |
| R16 | Batch DYNAMIC site changed (`LinkDepartmentsSheet.tsx`) | sha256 pin | `batch-allowlist.py --check` |
| R17 | `tsc` out of memory locally | cap, retry, CI | reported honestly in the result |
| R18 | Upstream changes how the current facility is found | anchor on `useCurrentFacility.tsx` (§6.3) | checker removal case |

Not covered (stated): plugin routes and plugin loaders attached at run time get the prefix rule
or `open` (parity); plugin pages for restricted roles are out of scope.

## 9. Owner decision (recorded)

**Q-K170 — answered B by the owner, 2026-11-24** (`docs/QUESTIONS.md`). The
`/_app/organization/<any>/patient/...` patient and visit pages get the same gates as the facility
ones (§3.3 rules 2 and 8, §3.4 row 3). The other pages that show patient or visit data stay open
as today but are listed by id in `OPEN_CLINICAL_IDS` (§3.3), and any new CARE route with patient
or visit data outside the gated prefixes fails CI until classified (§3.7.4). A role-by-role
walk-through of `OPEN_CLINICAL_IDS` is backlog (TRACKER row, §7.5).

No other owner decision is needed: code vs file routes (§4), the hook point (§3), the commit
shape (§7.1) and the failed-match and cross-facility rules (§3.2, §3.4) are technical choices
made here.

## 10. Done criteria (builder)
1. `apps/frontend` equals upstream `91b028f3aa33` except rows in `docs/UPSTREAM_PATCHES.md`;
   `upstream.lock` updated; no `raviger` import anywhere.
2. §3.7 tests pass on the real `routeTree.gen.ts` (quote the counts).
3. Restricted roles see `IhrNotForRole` / the status page exactly where they did before (Playwright
   `roles-menus`, `front-desk`, `billing-desk`), and on the organization patient and visit pages.
4. Signed-out visit to a staff URL → `/login?redirect=<absolute>` → Keycloak → back to that path
   and query (`preflight.spec.ts`, §5.2).
5. §7.4 proofs pass (quote commands and counts); CI green on the pushed head; no hook skipped.
6. If blocked or out of scope (e.g. §4.2 finds neither redirect form works and file routes are
   needed, or a new upstream behaviour changes the area guards or §1.4), stop and report.

## 11. Builder effort
Opus · high for §3, §4.1, §5, §6.3 (security boundary: gates, session, anchors); Sonnet · high for
the conflict re-application and the screen ports (§7.2 groups 5-7). Size: about 60-100 tool
calls; roughly 1.0-1.5M tokens for build and fix, before the review (full review shape: access and
session are security-sensitive).

Graphify calls made for this design: 7 (first draft: `graphify explain` for routeGate,
watcherScope, withIhrRoutes, IhrGrantWatcher, startSignIn, safeNext, endExpiredSession) + 4
(revision: useCurrentFacilitySilently, useIhrAccess, useIhrPatientView, MyAccessView). The graph
covers the pre-merge tree only; tip facts come from `git show upstream-frontend/develop:<path>`.
