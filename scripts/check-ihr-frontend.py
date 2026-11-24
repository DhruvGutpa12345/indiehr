#!/usr/bin/env python3
"""Our web-app menus and gates still fit CARE's source (P1-T11c1, design-c.md §5.3, §13.2 FF6).

Our code names text in upstream files: URL suffixes in the sidebar link lists
(facility-nav.tsx, location-nav.tsx), route ids of the generated TanStack route tree
(routeTree.gen.ts), and hooks we placed inside upstream files. An upstream sync that renames or
drops one would leave a menu silently open or closed (menu.ts fails toward CARE) or a gate on a
route that no longer exists. This check reads the sources and fails with the file and the anchor.

It checks:
1. every URL suffix named in apps/frontend/src/Utils/ihr/menu.ts exists in the nav file its
   section belongs to (facility section: facility-nav.tsx; location section: location-nav.tsx),
   and facility-nav.tsx still has the one childless Settings item (`${baseUrl}/settings/general`; CARE 3.1
   moved the 15 pages and Templates to the settings sidebar, P2-T0b);
   in the other direction, every `${baseUrl}/…` url of the two nav files is classified in its
   menu.ts section (named exactly, or under a prefix literal ending in "/"), so a NEW upstream
   item fails here until menu.ts decides it; facility-nav.tsx's baseUrl is still
   `/facility/${selectedFacility.id}`, the base ihrMenu.tsx rebuilds;
2. (P2-T0c, docs/reviews/p2-t0c/design.md §6.3) every `export const …_ID = "…"` of
   src/Utils/ihr/routeGates.ts is a key of FileRoutesById in src/routeTree.gen.ts (fast local
   feedback; routeGates.test.ts is the full check), and each DEMOGRAPHICS_TABS name is a patient
   tab route; our two hook points on CARE's router are in place (router.tsx
   `withIhrRouteTree(routeTree)`, AppShell.tsx `<IhrStaffPage>{children}</IhrStaffPage>` inside
   its ErrorBoundary, the only `{children}` there); IhrStaffPage.tsx keeps the watcher around
   the gate with the leaf's facility; ihrRouteTree.tsx still replaces CARE's auth pages by
   redirects to /login and Login.tsx renders IhrStaffSignIn, not CARE's password form; the
   upstream text our gates rely on is unchanged (the area guards of `_app`, `_public`,
   `_patient`, the root route's AreaChrome, AreaChrome's staff shell and `isAreaAllowed`, the
   "app" area rule in lib/router/auth.ts, and how useCurrentFacility.tsx finds the facility);
   and no route file (src/routes/**, lib/router/pluginRoutes.tsx) fetches data before our
   render-time gate (a `loader`, `loaderDeps`, `queryClient`, `ensureQueryData`,
   `prefetchQuery`, `prefetchInfiniteQuery` or `fetchQuery` runs on hover preload too);
3. each upstream file we hooked still has our marker and call (facility-nav.tsx,
   location-nav.tsx, overview.tsx, and the P1-T11c2 screens: PatientProfile.tsx, Demography.tsx,
   PatientRegistration.tsx, EncounterHistory.tsx, EncounterCard.tsx, EncounterActions.tsx,
   PatientHome.tsx, PatientInfoCard.tsx, CreateEncounterForm.tsx, EncounterList.tsx,
   AccountShow.tsx, ChargeItemsTable.tsx, BedChargeItemsTable.tsx), each hook site that carries a
   leak or a dead end (not one hook per file); and the upstream text the rules of src/Utils/ihr/frontDesk.ts read: the
   plugin-slot key and `BASE_PATIENT_TABS` export, the account tab names (ACCOUNT_TABS equals the
   keys of frontDesk.ts's ACCOUNT_TAB_SLUGS), the encounter list's refused filter keys;
3c. (P1-T11c3) IhrPatientActions (records button and live-grant banner) is still mounted and its
   marked import kept in PatientProfile.tsx and PatientHome.tsx, with the upstream blocks it sits
   beside; the chart tab `clinical_history` exists;
4. plugins/ihr_platform/ihr_roles/frontend_slugs.json (written by the backend's
   `manage.py ihr_frontend_slugs --write`) equals src/Utils/ihr/ihrSlugs.json byte for byte.

  python3 scripts/check-ihr-frontend.py      (exit 1 on any miss)
Tests: python3 -m unittest scripts/test_check_ihr_frontend.py
Stdlib only.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path("apps") / "frontend" / "src"
FACILITY_NAV = SRC / "components/ui/sidebar/facility/facility-nav.tsx"
LOCATION_NAV = SRC / "components/ui/sidebar/facility/location/location-nav.tsx"
PATIENT_TABS = SRC / "components/Patient/PatientDetailsTab/index.tsx"
IHR_MENU = SRC / "components/ihr/ihrMenu.tsx"
# P2-T0c: our hook points on CARE's TanStack router and the upstream text they rely on
ROUTER = SRC / "router.tsx"
APP_SHELL = SRC / "Routers/AppShell.tsx"
AREA_CHROME = SRC / "Routers/AreaChrome.tsx"
ROUTES_DIR = SRC / "routes"
ROOT_ROUTE = ROUTES_DIR / "__root.tsx"
APP_AREA = ROUTES_DIR / "_app.tsx"
PUBLIC_AREA = ROUTES_DIR / "_public.tsx"
PATIENT_AREA = ROUTES_DIR / "_patient.tsx"
ROUTER_AUTH = SRC / "lib/router/auth.ts"
PLUGIN_ROUTES = SRC / "lib/router/pluginRoutes.tsx"
CURRENT_FACILITY = SRC / "pages/Facility/utils/useCurrentFacility.tsx"
ROUTE_TREE = SRC / "routeTree.gen.ts"
IHR_STAFF_PAGE = SRC / "components/ihr/IhrStaffPage.tsx"
IHR_ROUTE_TREE = SRC / "components/ihr/ihrRouteTree.tsx"
LOGIN = SRC / "components/Auth/Login.tsx"
OVERVIEW = SRC / "pages/Facility/overview.tsx"
PATIENT_PROFILE = SRC / "components/Patient/PatientProfile.tsx"
DEMOGRAPHY = SRC / "components/Patient/PatientDetailsTab/Demography.tsx"
PATIENT_REGISTRATION = SRC / "components/Patient/PatientRegistration.tsx"
ENCOUNTER_HISTORY = SRC / "components/Patient/PatientDetailsTab/EncounterHistory.tsx"
ENCOUNTER_CARD = SRC / "components/Facility/EncounterCard.tsx"
ENCOUNTER_ACTIONS = SRC / "components/Encounter/EncounterActions.tsx"
PATIENT_HOME = SRC / "pages/Patient/PatientHome.tsx"
PATIENT_INFO_CARD = SRC / "components/Patient/PatientInfoCard.tsx"
CREATE_ENCOUNTER = SRC / "components/Encounter/CreateEncounterForm.tsx"
ENCOUNTER_LIST = SRC / "pages/Encounters/EncounterList.tsx"
ACCOUNT_SHOW = SRC / "pages/Facility/billing/account/AccountShow.tsx"
CHARGE_ITEMS_TABLE = SRC / "pages/Facility/billing/account/components/ChargeItemsTable.tsx"
USER_SUMMARY = SRC / "components/Users/UserSummary.tsx"
USER_FORM = SRC / "components/Users/UserForm.tsx"
NAV_USER = SRC / "components/ui/sidebar/nav-user.tsx"
BED_CHARGE_ITEMS_TABLE = SRC / "pages/Facility/billing/account/components/BedChargeItemsTable.tsx"
BOOKINGS_LIST = SRC / "pages/Appointments/BookAppointment/BookingsList.tsx"
UPCOMING_APPOINTMENT = SRC / "pages/Appointments/components/UpcomingAppointmentCard.tsx"
FRONT_DESK = SRC / "Utils/ihr/frontDesk.ts"
MENU = SRC / "Utils/ihr/menu.ts"
ROUTE_GATES = SRC / "Utils/ihr/routeGates.ts"
SLUGS_COPY = SRC / "Utils/ihr/ihrSlugs.json"
SLUGS_SOURCE = Path("plugins/ihr_platform/ihr_roles/frontend_slugs.json")

FILES = [
    FACILITY_NAV,
    LOCATION_NAV,
    PATIENT_TABS,
    IHR_MENU,
    ROUTER,
    APP_SHELL,
    AREA_CHROME,
    ROOT_ROUTE,
    APP_AREA,
    PUBLIC_AREA,
    PATIENT_AREA,
    ROUTER_AUTH,
    PLUGIN_ROUTES,
    CURRENT_FACILITY,
    ROUTE_TREE,
    IHR_STAFF_PAGE,
    IHR_ROUTE_TREE,
    LOGIN,
    OVERVIEW,
    PATIENT_PROFILE,
    DEMOGRAPHY,
    PATIENT_REGISTRATION,
    ENCOUNTER_HISTORY,
    ENCOUNTER_CARD,
    ENCOUNTER_ACTIONS,
    PATIENT_HOME,
    PATIENT_INFO_CARD,
    CREATE_ENCOUNTER,
    ENCOUNTER_LIST,
    ACCOUNT_SHOW,
    CHARGE_ITEMS_TABLE,
    BED_CHARGE_ITEMS_TABLE,
    USER_SUMMARY,
    USER_FORM,
    NAV_USER,
    BOOKINGS_LIST,
    UPCOMING_APPOINTMENT,
    FRONT_DESK,
    MENU,
    ROUTE_GATES,
    SLUGS_COPY,
    SLUGS_SOURCE,
]

MARKER = "IndieHR P1-T11c"
# Directories read whole (besides FILES): every route file is checked for data fetches
DIRS = [ROUTES_DIR]

# file -> text our hook must still contain (besides the marker)
HOOKS = {
    FACILITY_NAV: "<IhrFacilityNavMain",
    LOCATION_NAV: "<IhrLocationNavMain",
    OVERVIEW: "ihrShowsServices(",
    # P1-T11c2 (frontDesk.ts rules)
    PATIENT_PROFILE: "ihrPatientTabs(",
    DEMOGRAPHY: "ihrDemographySections(",
    PATIENT_REGISTRATION: "ihrHidesClinicalFormFields(",
    ENCOUNTER_HISTORY: "ihrEncounterHistoryRule(",
    ENCOUNTER_CARD: "ihrCanOpenEncounter(",
    ENCOUNTER_ACTIONS: "ihrShowsEncounterActions(",
    PATIENT_HOME: "ihrShowsViewAccounts(",
    PATIENT_INFO_CARD: "hideTags = false",
    CREATE_ENCOUNTER: "ihrShowsEncounterTags(",
    ENCOUNTER_LIST: "ihrEncounterParams(",
    ACCOUNT_SHOW: "ihrAccountTabs(",
    CHARGE_ITEMS_TABLE: "canEditChargeItems = true",
    BED_CHARGE_ITEMS_TABLE: "canEditChargeItems = true",
}

# P2-T0c: our two hook points on CARE's router (marker MARKER_ROUTER), file -> call
MARKER_ROUTER = "IndieHR P2-T0c"
ROUTER_HOOKS = {
    ROUTER: "routeTree: withIhrRouteTree(routeTree)",
    APP_SHELL: "<IhrStaffPage>",
}
# (file, regex that must match (DOTALL), why[, how many times exactly; default: at least once])
ROUTER_ANCHORS = [
    (
        APP_SHELL,
        r"<ErrorBoundary\b(?:(?!</ErrorBoundary>).)*?<IhrStaffPage>\s*\{children\}\s*</IhrStaffPage>\s*</ErrorBoundary>",
        "the staff page outlet inside our gate, inside CARE's ErrorBoundary",
    ),
    (APP_SHELL, r"\{children\}", "the page rendered once only (inside IhrStaffPage)", 1),
    (APP_SHELL, r'import \{ IhrStaffPage \} from "@/components/ihr/IhrStaffPage";', "the marked import"),
    (ROUTER, r'import \{ withIhrRouteTree \} from "@/components/ihr/ihrRouteTree";', "the import of our route tree"),
    (
        IHR_STAFF_PAGE,
        r"<IhrGrantWatcher\b(?:(?!</IhrGrantWatcher>).)*<IhrRouteGate\b(?:(?!</IhrGrantWatcher>).)*</IhrGrantWatcher>",
        "the one grant watcher around the gate",
    ),
    (IHR_STAFF_PAGE, r"facilityId=\{leaf\.facilityId\}", "the watcher watches the leaf's facility, never the address bar's"),
    (IHR_STAFF_PAGE, r"useMatches\(\{\s*select:\s*leafSignature\s*\}\)", "the committed matches, deepest non-failed match"),
    (IHR_ROUTE_TREE, r'throw redirect\(\{\s*to:\s*"/login"', "CARE's auth pages redirect to /login"),
    (IHR_ROUTE_TREE, r'from "@/routes/_public/2fa"', "the 2FA page is replaced"),
    (IHR_ROUTE_TREE, r'from "@/routes/_public/forgot-password"', "the forgot-password page is replaced"),
    (IHR_ROUTE_TREE, r'from "@/routes/_public/password_reset\.\$token"', "the password-reset page is replaced"),
    (IHR_ROUTE_TREE, r"getParentRoute:\s*\(\)\s*=>\s*AppRoute\b", "our pages are staff pages (under /_app)"),
    (LOGIN, r"<IhrStaffSignIn\s*/>", "the staff panel is our Keycloak sign-in"),
    # upstream text our gates rely on (unmarked)
    (APP_AREA, r"beforeLoad:\s*requireStaff\b", "the staff area guard"),
    (PUBLIC_AREA, r"beforeLoad:\s*publicOnly\b", "the public area guard"),
    (PATIENT_AREA, r"beforeLoad:\s*requirePatient\b", "the patient area guard"),
    (ROOT_ROUTE, r"<AreaChrome\b", "the root route renders each area's chrome"),
    (AREA_CHROME, r'case "app":\s*chrome = <AppShell>', "the staff area renders AppShell (our hook point)"),
    (AREA_CHROME, r"isAreaAllowed\(", "a page renders only for its area's session"),
    (ROUTER_AUTH, r'if \(area === "app"\) return status === "user";', "staff pages only for a signed-in staff user"),
    (CURRENT_FACILITY, r"usePathname\(\)", "the address-bar facility the gate compares (design §1.4, §3.4 row 1)"),
    (CURRENT_FACILITY, r'segments\[1\] === "facility"', "the address-bar facility rule routeGates.ts facilityFromPath copies"),
]
# (file, regex that must NOT match, why)
ROUTER_ABSENT = [
    (LOGIN, r"<StaffLoginForm\b", "CARE's password form (staff sign in through Keycloak)"),
    (LOGIN, r"<ForgotPassword\b", "CARE's forgot-password form"),
]
# No data fetch before our render-time gate (design §6.3, attack S3): a loader or a beforeLoad
# fetch runs on hover preload and on navigation, before IhrRouteGate. (regex, what)
FETCH_PATTERNS = [
    (r"\bloader(?:Deps)?\b", "a route loader"),
    (r"queryClient|ensureQueryData|prefetch(?:Infinite)?Query|fetchQuery", "a query-client fetch"),
    # P2-T0c review F2: this codebase's own fetch helpers, and any awaited (async) step
    (r"\bcallApi\(|\bquery(?:\.\w+)?\(|\bfetch\(|\brequest\(", "a fetch helper"),
    (r"\basync\b|\bawait\b", "an awaited step (a guard that waits may fetch)"),
]
ROUTE_ID_CONST = re.compile(r'export const (\w+_ID)\s*=\s*\n?\s*"([^"]+)"')
TREE_BLOCK_START = "export interface FileRoutesById {"
TREE_ID = re.compile(r'^\s+(?:"([^"]+)"|(__root__)): typeof ', re.M)

# P1-T11c2: upstream text the rules of frontDesk.ts read: (file, regex that must match, why[, how
# many times at least; default 1]). Regexes, not literals: prettier may break a call over several
# lines. Every hook site that carries a leak or a dead end is listed, so a sync resolution that
# drops one is caught (a counted entry: one site per button).
FRONT_DESK_ANCHORS = [
    (DEMOGRAPHY, r'key="patient_details_tab__demography__general_info"', "the plugin slot ihrDemographySections removes"),
    (DEMOGRAPHY, r'id: "additional-details"', "the plugin-data section it removes"),
    (DEMOGRAPHY, r'id: "tags"', "the tag section it removes"),
    (DEMOGRAPHY, r'id: "general-info"', "the general section it edits"),
    (PATIENT_TABS, r"export const BASE_PATIENT_TABS\b", "the unfiltered tab list PatientProfile passes"),
    (ENCOUNTER_LIST, r'departmentFilter\(\s*"organization"', "the department filter key"),
    (ENCOUNTER_LIST, r'careTeamFilter\(\s*"care_team"', "the care team filter key"),
    (ENCOUNTER_LIST, r'tagFilter\(\s*"tags"', "the tag filter key"),
    (ENCOUNTER_LIST, r"restoredParams\.organization\b", "the session-restored filters (F18)"),
    (ENCOUNTER_LIST, r"restoredParams\.care_team_user\b", "the session-restored filters (F18)"),
    (ENCOUNTER_LIST, r"restoredParams\.tags\b", "the session-restored filters (F18)"),
    (ACCOUNT_SHOW, r"canAddChargeItems=\{ihrCanAddChargeItems\(", "the add-charge-items buttons' rule"),
    (ACCOUNT_SHOW, r"<IhrNavTabs\b", "NavTabs that renders nothing without a visible tab"),
    # patient home: the tag card and the encounter tab's list right
    (PATIENT_HOME, r"hideTags=\{ihrHidesPatientTags\(", "the tag card (projected as empty, a refused write)"),
    (PATIENT_HOME, r"ihrCanListEncounters\(", "the encounter tab's list right"),
    # encounter list: the filters offered and the request switch
    (ENCOUNTER_LIST, r"ihrEncounterFilters\(", "the filter chips the backend refuses"),
    (ENCOUNTER_LIST, r"ihrEncounterListEnabled\(", "no list request while my-access is pending"),
    # patient edit form: blood group, plugin fields and the death record (patient_edit.txt)
    (PATIENT_REGISTRATION, r"hideBloodGroup=\{ihrHidesClinical\}", "the blood group field"),
    (PATIENT_REGISTRATION, r"!ihrHidesClinical\s*&&\s*extensions\.fields", "the plugin extension fields"),
    (PATIENT_REGISTRATION, r"hideDeceased=\{ihrHidesClinical\}", "the death record (400 when changed)"),
    (PATIENT_REGISTRATION, r"!hideDeceased\s*&&", "the death record block"),
    # account page: every write button follows the role's own right (ihrAccountActions)
    (ACCOUNT_SHOW, r"ihrAccountActions\(ihr\)", "the account page's write rule"),
    (ACCOUNT_SHOW, r"ihrActions\.invoice\s*&&", "Create invoice (desktop and mobile)", 2),
    (ACCOUNT_SHOW, r"ihrActions\.payment\s*&&", "credit payment (desktop, mobile), credit note, transfer", 4),
    (ACCOUNT_SHOW, r"ihrActions\.account\s*&&", "Settle & close (desktop, menu) and the tag sheet", 3),
    (ACCOUNT_SHOW, r"\(ihrActions\.account\s*\|\|\s*ihrActions\.payment\)", "the header menu only when it has an item"),
    (ACCOUNT_SHOW, r"canEditChargeItems=\{ihrActions\.chargeItems\}", "the charge item row menus (both tables)", 2),
    (CHARGE_ITEMS_TABLE, r"\{canEditChargeItems\s*&&", "the row menu"),
    # the header box also needs a movable item (CARE 3.1): `disabled={!movableChargeItems.length || !canEditChargeItems}`
    (CHARGE_ITEMS_TABLE, r"disabled=\{\s*(?:!movableChargeItems\.length\s*\|\|\s*)?!canEditChargeItems\s*\}", "the bulk select (header and row)", 2),
    (BED_CHARGE_ITEMS_TABLE, r"\{canEditChargeItems\s*&&", "the row menu"),
    # a projected booking has no schedule (fields/booking.txt drops token_slot.availability,
    # Q-K37): the patient's Appointments tab and patient home must not read its name unguarded
    (BOOKINGS_LIST, r"token_slot\.availability\?\.name", "the schedule name (card and table)", 2),
    (UPCOMING_APPOINTMENT, r"token_slot\.availability\?\.name", "the schedule name"),
]
# P1-T11c3: the records-access button and live-grant banner (IhrPatientActions) sit in two
# upstream screens (the grant watcher's hook point is in ROUTER_ANCHORS since P2-T0c).
# (file, regex that must match, why[, how many times at least; default 1])
RECORDS_ANCHORS = [
    (PATIENT_PROFILE, r"<IhrPatientActions\b", "the records button and live-grant banner (MRD)"),
    (PATIENT_PROFILE, r"import \{ IhrPatientActions \}[^\n]*IndieHR P1-T11c", "the marked import of the banner"),
    (PATIENT_PROFILE, r"<PatientDeceasedInfo\b", "the header block the banner sits below"),
    (PATIENT_HOME, r"<IhrPatientActions\b", "the records button and live-grant banner (MRD)"),
    (PATIENT_HOME, r"import \{ IhrPatientActions \}[^\n]*IndieHR P1-T11c", "the marked import of the banner"),
    (PATIENT_HOME, r"</PatientInfoCard>", "the card the banner sits below"),
    (PATIENT_TABS, r'route: "clinical_history"', "the chart tab MRD sees under a grant (tests-ihr/records.spec.ts)"),
]
# P1-T24b (review W6): the staff "Sign-in" card's mount on the user page and the add-user form's
# one option. Both are small edits in upstream files that an upstream sync could silently undo.
# (file, regex that must match, why[, how many times at least; default 1])
STAFF_ANCHORS = [
    (USER_SUMMARY, r"<IhrStaffAccessCard\b", "the Sign-in card (resend, cancel, reset) on a user page"),
    (USER_SUMMARY, r"import \{ IhrStaffAccessCard \}", "the card's import"),
    (USER_FORM, r'password_setup_method:\s*"email"', "the add-user form defaults to the e-mail invitation"),
    (USER_FORM, r"IndieHR P1-T24b", "the marker of the removed 'set password now' option"),
]
# (file, regex that must NOT match, why): a sync that brings it back reopens a removed path
STAFF_ABSENT = [
    (USER_FORM, r'value="immediate"', "the removed 'set password now' radio (an admin never sets a password)"),
]
# P1-T31: the hospital switcher's one mount in the staff user menu (an upstream sync could drop it)
SWITCHER_ANCHORS = [
    (NAV_USER, r"<IhrHospitalsMenu\b", "the Hospitals group in the user menu"),
    (NAV_USER, r"import \{ IhrHospitalsMenu \}", "its import"),
]
ACCOUNT_TABS_UPSTREAM = re.compile(r"export const ACCOUNT_TABS = \[([^\]]*)\]")
ACCOUNT_TAB_SLUGS_OURS = re.compile(r"ACCOUNT_TAB_SLUGS[^=]*=\s*\{([^}]*)\}")


FACILITY_SECTION = "// Facility menu (facility-nav.tsx)"
LOCATION_SECTION = "// Location menu (location-nav.tsx)"
# CARE 3.1 (P2-T0b): the facility menu has one childless Settings item; the pages are in the
# settings sidebar (facility-settings-nav.tsx)
SETTINGS_URLS = 1
LITERAL = re.compile(r'"(/[A-Za-z0-9_/.-]*)"')
DEMOGRAPHICS_TABS = re.compile(r"export const DEMOGRAPHICS_TABS\s*=\s*\[([^\]]*)\]")
# a url of a nav file: `${baseUrl}/path` up to the closing quote or a `${…}`
NAV_URL = re.compile(r"\$\{baseUrl\}(/[^`\"$]*)")
FACILITY_BASE_UPSTREAM = "const baseUrl = `/facility/${selectedFacility.id}`;"
FACILITY_BASE_OURS = "`/facility/${facilityId}`"


def _read(root: Path, rel: Path, problems: list) -> str:
    path = root / rel
    if not path.is_file():
        problems.append(f"{rel}: file is missing")
        return ""
    return path.read_text(encoding="utf-8")


def _menu_sections(menu: str):
    """The menu.ts text of the facility section and of the location section."""
    f = menu.find(FACILITY_SECTION)
    loc = menu.find(LOCATION_SECTION)
    if f < 0 or loc < 0 or loc < f:
        return None
    return menu[f:loc], menu[loc:]


def _suffix_present(nav: str, literal: str) -> bool:
    """`${baseUrl}<literal>` is a url in the nav file (a prefix literal ends with "/")."""
    pattern = r"\$\{baseUrl\}" + re.escape(literal)
    if not literal.endswith("/"):
        pattern += r"[`\"]"
    return re.search(pattern, nav) is not None


def _classified(path: str, literals: list) -> bool:
    """menu.ts names this url suffix, or a prefix literal (ending in "/") covers it."""
    return path in literals or any(
        lit.endswith("/") and path.startswith(lit) for lit in literals
    )


def fetch_problems(root: Path) -> list:
    """A data fetch in a route file runs before our render-time gate (design §6.3)."""
    problems = []
    paths = [root / PLUGIN_ROUTES]
    routes = root / ROUTES_DIR
    if routes.is_dir():
        paths += sorted(p for p in routes.rglob("*") if p.suffix in (".ts", ".tsx") and p.is_file())
    else:
        problems.append(f"{ROUTES_DIR}: directory is missing")
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(root)
        for pattern, what in FETCH_PATTERNS:
            for match in re.finditer(pattern, text):
                line = text.count("\n", 0, match.start()) + 1
                problems.append(
                    f"{rel}:{line}: `{match.group(0)}` ({what}) runs before the IndieHR route gate, also on hover preload: review it (docs/reviews/p2-t0c/design.md §6.3)"
                )
    return problems


def check(root: Path = ROOT) -> list:
    problems: list = []
    texts = {rel: _read(root, rel, problems) for rel in FILES}

    # 1. menu suffixes
    sections = _menu_sections(texts[MENU])
    if texts[MENU] and sections is None:
        problems.append(
            f"{MENU}: section markers {FACILITY_SECTION!r} and {LOCATION_SECTION!r} not found in that order"
        )
    elif sections:
        for (name, nav_rel), section in zip(
            (("facility", FACILITY_NAV), ("location", LOCATION_NAV)), sections
        ):
            nav = texts[nav_rel]
            literals = sorted(set(LITERAL.findall(section)))
            if not literals:
                problems.append(f"{MENU}: no url suffix found in the {name} section")
            for literal in literals:
                if nav and not _suffix_present(nav, literal):
                    problems.append(
                        f"{nav_rel}: url suffix {literal!r} (named in {MENU}, {name} menu) not found as ${{baseUrl}}{literal}"
                    )
            # the other direction: a new upstream item must be classified first
            for path in sorted(set(NAV_URL.findall(nav))):
                if path == "/":  # `${baseUrl}/${l.url}`: plugin links, as CARE has them
                    continue
                if literals and not _classified(path, literals):
                    problems.append(
                        f"{nav_rel}: url ${{baseUrl}}{path} is not classified in {MENU} ({name} menu): add its rule"
                    )
    nav = texts[FACILITY_NAV]
    if nav:
        if len(re.findall(r"\$\{baseUrl\}/settings/", nav)) != SETTINGS_URLS:
            problems.append(
                f"{FACILITY_NAV}: expected exactly {SETTINGS_URLS} settings item (${{baseUrl}}/settings/…, childless): classify the change in {MENU}"
            )
        if FACILITY_BASE_UPSTREAM not in nav:
            problems.append(
                f"{FACILITY_NAV}: `{FACILITY_BASE_UPSTREAM}` not found ({IHR_MENU} rebuilds this base for the menu rules)"
            )
        if "<EncounterNavItem" not in nav:
            problems.append(
                f"{FACILITY_NAV}: the Encounters item's element (<EncounterNavItem, the class lists) not found"
            )

    if texts[IHR_MENU] and FACILITY_BASE_OURS not in texts[IHR_MENU]:
        problems.append(f"{IHR_MENU}: the facility base {FACILITY_BASE_OURS} not found")

    # 2. route ids, our router hook points, the upstream text they rely on (P2-T0c)
    tree_ids = set()
    if texts[ROUTE_TREE]:
        begin = texts[ROUTE_TREE].find(TREE_BLOCK_START)
        if begin < 0:
            problems.append(f"{ROUTE_TREE}: `{TREE_BLOCK_START}` not found")
        else:
            finish = texts[ROUTE_TREE].find("\n}", begin)
            block = texts[ROUTE_TREE][begin:finish]
            tree_ids = {a or b for a, b in TREE_ID.findall(block)}
    ids = ROUTE_ID_CONST.findall(texts[ROUTE_GATES])
    if texts[ROUTE_GATES] and not ids:
        problems.append(f"{ROUTE_GATES}: no `export const …_ID = \"…\"` found")
    if texts[ROUTE_GATES] and "ENCOUNTER_ID" not in {n for n, _ in ids}:
        problems.append(f"{ROUTE_GATES}: ENCOUNTER_ID is not defined as a string constant")
    if tree_ids:
        for name, route_id in ids:
            if route_id not in tree_ids:
                problems.append(
                    f"{ROUTE_TREE}: route id \"{route_id}\" not found in FileRoutesById ({ROUTE_GATES} {name})"
                )
    tabs = DEMOGRAPHICS_TABS.search(texts[ROUTE_GATES])
    if texts[ROUTE_GATES] and tabs is None:
        problems.append(f"{ROUTE_GATES}: DEMOGRAPHICS_TABS not found")
    if tabs:
        for tab in re.findall(r'"([^"]+)"', tabs.group(1)):
            if f'route: "{tab}"' not in texts[PATIENT_TABS]:
                problems.append(
                    f"{PATIENT_TABS}: patient tab route \"{tab}\" not found (routeGates DEMOGRAPHICS_TABS)"
                )
    for rel, call in ROUTER_HOOKS.items():
        text = texts[rel]
        if not text:
            continue
        if MARKER_ROUTER not in text:
            problems.append(f"{rel}: our hook marker `{MARKER_ROUTER}` is gone")
        if call not in text:
            problems.append(f"{rel}: our hook call `{call}` is gone")
    for rel, pattern, why, *exactly in ROUTER_ANCHORS:
        if not texts[rel]:
            continue
        found = len(re.findall(pattern, texts[rel], flags=re.S))
        if (exactly and found != exactly[0]) or (not exactly and found < 1):
            expected = exactly[0] if exactly else "at least 1"
            problems.append(
                f"{rel}: `{pattern}` found {found} time(s), expected {expected} ({why})"
            )
    for rel, pattern, why in ROUTER_ABSENT:
        if texts[rel] and re.search(pattern, texts[rel]):
            problems.append(f"{rel}: `{pattern}` is back ({why})")
    problems.extend(fetch_problems(root))

    # 3. hooks
    for rel, call in HOOKS.items():
        text = texts[rel]
        if not text:
            continue
        if MARKER not in text:
            problems.append(f"{rel}: our hook marker `{MARKER}` is gone")
        if call not in text:
            problems.append(f"{rel}: our hook call `{call}` is gone")

    # 3b. the upstream text frontDesk.ts's rules read (P1-T11c2)
    for rel, pattern, why, *least in FRONT_DESK_ANCHORS:
        needed = least[0] if least else 1
        found = len(re.findall(pattern, texts[rel])) if texts[rel] else needed
        if found < needed:
            problems.append(
                f"{rel}: `{pattern}` found {found} time(s), expected {needed} ({why}; {FRONT_DESK})"
            )
    # 3c. the records-access hooks and the watcher (P1-T11c3)
    for rel, pattern, why, *least in RECORDS_ANCHORS:
        needed = least[0] if least else 1
        found = len(re.findall(pattern, texts[rel])) if texts[rel] else needed
        if found < needed:
            problems.append(
                f"{rel}: `{pattern}` found {found} time(s), expected {needed} ({why})"
            )
    # 3d. the staff sign-in card and the add-user form (P1-T24b)
    for rel, pattern, why, *least in STAFF_ANCHORS:
        needed = least[0] if least else 1
        found = len(re.findall(pattern, texts[rel])) if texts[rel] else needed
        if found < needed:
            problems.append(
                f"{rel}: `{pattern}` found {found} time(s), expected {needed} ({why})"
            )
    for rel, pattern, why in STAFF_ABSENT:
        if texts[rel] and re.search(pattern, texts[rel]):
            problems.append(f"{rel}: `{pattern}` is back ({why})")
    # 3e. the hospital switcher's mount in the user menu (P1-T31)
    for rel, pattern, why, *least in SWITCHER_ANCHORS:
        needed = least[0] if least else 1
        found = len(re.findall(pattern, texts[rel])) if texts[rel] else needed
        if found < needed:
            problems.append(
                f"{rel}: `{pattern}` found {found} time(s), expected {needed} ({why})"
            )
    upstream_tabs = ACCOUNT_TABS_UPSTREAM.search(texts[ACCOUNT_SHOW])
    ours_tabs = ACCOUNT_TAB_SLUGS_OURS.search(texts[FRONT_DESK])
    if texts[ACCOUNT_SHOW] and texts[FRONT_DESK]:
        if upstream_tabs is None or ours_tabs is None:
            problems.append(
                f"{ACCOUNT_SHOW}: ACCOUNT_TABS (or {FRONT_DESK}: ACCOUNT_TAB_SLUGS) not found"
            )
        else:
            theirs = re.findall(r'"([^"]+)"', upstream_tabs.group(1))
            mine = re.findall(r"^\s*(\w+):", ours_tabs.group(1), flags=re.M)
            if sorted(theirs) != sorted(mine):
                problems.append(
                    f"{ACCOUNT_SHOW}: ACCOUNT_TABS {theirs} differs from the tabs {FRONT_DESK} classifies {mine}: classify the change in ACCOUNT_TAB_SLUGS"
                )

    # 4. slug files
    src, copy = root / SLUGS_SOURCE, root / SLUGS_COPY
    if src.is_file() and copy.is_file() and src.read_bytes() != copy.read_bytes():
        problems.append(
            f"{SLUGS_COPY} differs from {SLUGS_SOURCE}: run `manage.py ihr_frontend_slugs --write` and copy the file"
        )
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("ihr web-app anchors:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("ihr web-app anchors: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
