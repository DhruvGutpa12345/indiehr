#!/usr/bin/env python3
"""Our web-app menus and gates still fit CARE's source (P1-T11c1, design-c.md §5.3, §13.2 FF6).

Our code names text in upstream files: URL suffixes in the sidebar link lists
(facility-nav.tsx, location-nav.tsx), route keys in the route tables (ConsultationRoutes.tsx,
PatientRoutes.tsx), and hooks we placed inside upstream files. An upstream sync that renames or
drops one would leave a menu silently open or closed (menu.ts fails toward CARE) or a gate on a
route that no longer exists. This check reads the sources and fails with the file and the anchor.

It checks:
1. every URL suffix named in apps/frontend/src/Utils/ihr/menu.ts exists in the nav file its
   section belongs to (facility section: facility-nav.tsx; location section: location-nav.tsx),
   and facility-nav.tsx still has the Settings parent and 15 `${baseUrl}/settings/…` items plus `/template`;
   in the other direction, every `${baseUrl}/…` url of the two nav files is classified in its
   menu.ts section (named exactly, or under a prefix literal ending in "/"), so a NEW upstream
   item fails here until menu.ts decides it; facility-nav.tsx's baseUrl is still
   `/facility/${selectedFacility.id}`, the base ihrMenu.tsx rebuilds;
2. every route key named in src/Utils/ihr/routeKeys.ts exists upstream: the encounter key
   as `/${identifier}/:id/patient/:patientId/encounter/:encounterId/:tab` in
   ConsultationRoutes.tsx, the others as string keys in the file KEY_FILES names
   (PatientRoutes.tsx by default); the patient tab keys as CARE builds them
   (`"<PATIENT_TAB_PREFIX>" + tab.route`) and each DEMOGRAPHICS_TABS name as a tab route;
3. each upstream file we hooked still has our marker and call (AppRouter.tsx, facility-nav.tsx,
   location-nav.tsx, overview.tsx, and the P1-T11c2 screens: PatientProfile.tsx, Demography.tsx,
   PatientRegistration.tsx, EncounterHistory.tsx, EncounterCard.tsx, EncounterActions.tsx,
   PatientHome.tsx, PatientInfoCard.tsx, CreateEncounterForm.tsx, EncounterList.tsx,
   AccountShow.tsx, ChargeItemsTable.tsx, BedChargeItemsTable.tsx), each hook site that carries a
   leak or a dead end (not one hook per file); and the upstream text the rules of src/Utils/ihr/frontDesk.ts read: the
   plugin-slot key and `BASE_PATIENT_TABS` export, the account tab names (ACCOUNT_TABS equals the
   keys of frontDesk.ts's ACCOUNT_TAB_SLUGS), the encounter list's refused filter keys;
3c. (P1-T11c3) IhrPatientActions (records button and live-grant banner) is still mounted and its
   marked import kept in PatientProfile.tsx and PatientHome.tsx, with the upstream blocks it sits
   beside; the chart tab `clinical_history` exists; every facility route (CARE's and ours) is
   still returned inside the one grant watcher by src/components/ihr/IhrRoutes.tsx;
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
CONSULTATION_ROUTES = SRC / "Routers/routes/ConsultationRoutes.tsx"
PATIENT_ROUTES = SRC / "Routers/routes/PatientRoutes.tsx"
SCHEDULE_ROUTES = SRC / "Routers/routes/ScheduleRoutes.tsx"
PATIENT_TABS = SRC / "components/Patient/PatientDetailsTab/index.tsx"
IHR_MENU = SRC / "components/ihr/ihrMenu.tsx"
IHR_ROUTES = SRC / "components/ihr/IhrRoutes.tsx"
APP_ROUTER = SRC / "Routers/AppRouter.tsx"
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
BED_CHARGE_ITEMS_TABLE = SRC / "pages/Facility/billing/account/components/BedChargeItemsTable.tsx"
FRONT_DESK = SRC / "Utils/ihr/frontDesk.ts"
MENU = SRC / "Utils/ihr/menu.ts"
ROUTE_KEYS = SRC / "Utils/ihr/routeKeys.ts"
SLUGS_COPY = SRC / "Utils/ihr/ihrSlugs.json"
SLUGS_SOURCE = Path("plugins/ihr_platform/ihr_roles/frontend_slugs.json")

FILES = [
    FACILITY_NAV,
    LOCATION_NAV,
    CONSULTATION_ROUTES,
    PATIENT_ROUTES,
    SCHEDULE_ROUTES,
    PATIENT_TABS,
    IHR_MENU,
    IHR_ROUTES,
    APP_ROUTER,
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
    FRONT_DESK,
    MENU,
    ROUTE_KEYS,
    SLUGS_COPY,
    SLUGS_SOURCE,
]

MARKER = "IndieHR P1-T11c"
# file -> text our hook must still contain (besides the marker)
HOOKS = {
    APP_ROUTER: "withIhrRoutes(",
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
    (CHARGE_ITEMS_TABLE, r"disabled=\{!canEditChargeItems\}", "the bulk select (header and row)", 2),
    (BED_CHARGE_ITEMS_TABLE, r"\{canEditChargeItems\s*&&", "the row menu"),
]
# P1-T11c3: the records-access button and live-grant banner (IhrPatientActions) sit in two
# upstream screens; every facility route is returned inside the one grant watcher (withIhrRoutes).
# (file, regex that must match, why[, how many times at least; default 1])
RECORDS_ANCHORS = [
    (PATIENT_PROFILE, r"<IhrPatientActions\b", "the records button and live-grant banner (MRD)"),
    (PATIENT_PROFILE, r"import \{ IhrPatientActions \}[^\n]*IndieHR P1-T11c", "the marked import of the banner"),
    (PATIENT_PROFILE, r"<PatientDeceasedInfo\b", "the header block the banner sits below"),
    (PATIENT_HOME, r"<IhrPatientActions\b", "the records button and live-grant banner (MRD)"),
    (PATIENT_HOME, r"import \{ IhrPatientActions \}[^\n]*IndieHR P1-T11c", "the marked import of the banner"),
    (PATIENT_HOME, r"</PatientInfoCard>", "the card the banner sits below"),
    (PATIENT_TABS, r'route: "clinical_history"', "the chart tab MRD sees under a grant (tests-ihr/records.spec.ts)"),
    (IHR_ROUTES, r"withWatcher\(key,\s*wrap\(key,\s*route\)\)", "every CARE facility route inside the one grant watcher"),
    (IHR_ROUTES, r"withWatcher\(key,\s*route\)", "our own facility routes inside the one grant watcher"),
    (IHR_ROUTES, r"<IhrGrantWatcher\b", "the watcher itself"),
    (IHR_ROUTES, r"watcherScope\(key\)", "the facility-less patient charts inside the watcher too (review F6)"),
]
ACCOUNT_TABS_UPSTREAM = re.compile(r"export const ACCOUNT_TABS = \[([^\]]*)\]")
ACCOUNT_TAB_SLUGS_OURS = re.compile(r"ACCOUNT_TAB_SLUGS[^=]*=\s*\{([^}]*)\}")


FACILITY_SECTION = "// Facility menu (facility-nav.tsx)"
LOCATION_SECTION = "// Location menu (location-nav.tsx)"
SETTINGS_ITEMS = 15
# the Settings parent links to /settings/general too, so its url appears one more time
SETTINGS_URLS = SETTINGS_ITEMS + 1
LITERAL = re.compile(r'"(/[A-Za-z0-9_/.-]*)"')
ROUTE_KEY = re.compile(r'export const (\w+_KEY)\s*=\s*"([^"]+)"')
ENCOUNTER_KEY_NAME = "ENCOUNTER_KEY"
# CARE builds the encounter route for "facility" and "organization" in one reduce
ENCOUNTER_UPSTREAM = r"/\$\{identifier\}/:id/patient/:patientId/encounter/:encounterId/:tab"
# route key constant -> the upstream table that defines it (default: PatientRoutes.tsx)
KEY_FILES = {
    "ENCOUNTER_QUESTIONNAIRE_KEY": CONSULTATION_ROUTES,
    "APPOINTMENT_KEY": SCHEDULE_ROUTES,
}
TAB_PREFIX = re.compile(r'export const PATIENT_TAB_PREFIX\s*=\s*"([^"]+)"')
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
                f"{FACILITY_NAV}: expected the Settings parent and {SETTINGS_ITEMS} settings items (${{baseUrl}}/settings/…): classify the change in {MENU}"
            )
        if not re.search(r"\$\{baseUrl\}/template[`\"]", nav):
            problems.append(f"{FACILITY_NAV}: the Templates item (${{baseUrl}}/template) not found")
        if FACILITY_BASE_UPSTREAM not in nav:
            problems.append(
                f"{FACILITY_NAV}: `{FACILITY_BASE_UPSTREAM}` not found ({IHR_MENU} rebuilds this base for the menu rules)"
            )
        if not re.search(r"\$\{baseUrl\}/encounters/patients/\$\{encounterClass\}", nav):
            problems.append(
                f"{FACILITY_NAV}: the encounter class items (${{baseUrl}}/encounters/patients/${{encounterClass}}) not found"
            )

    if texts[IHR_MENU] and FACILITY_BASE_OURS not in texts[IHR_MENU]:
        problems.append(f"{IHR_MENU}: the facility base {FACILITY_BASE_OURS} not found")

    # 2. route keys
    keys = ROUTE_KEY.findall(texts[ROUTE_KEYS])
    if texts[ROUTE_KEYS] and not keys:
        problems.append(f"{ROUTE_KEYS}: no `export const …_KEY = \"…\"` found")
    for name, key in keys:
        if name == ENCOUNTER_KEY_NAME:
            if not re.search(ENCOUNTER_UPSTREAM, texts[CONSULTATION_ROUTES]):
                problems.append(
                    f"{CONSULTATION_ROUTES}: encounter route `/${{identifier}}/:id/patient/:patientId/encounter/:encounterId/:tab` not found (routeKeys {name} = {key})"
                )
            continue
        table = KEY_FILES.get(name, PATIENT_ROUTES)
        if f'"{key}"' not in texts[table]:
            problems.append(f"{table}: route key \"{key}\" not found (routeKeys {name})")
    for name in (ENCOUNTER_KEY_NAME, *KEY_FILES):
        if texts[ROUTE_KEYS] and name not in {n for n, _ in keys}:
            problems.append(f"{ROUTE_KEYS}: {name} is not defined as a string constant")
    prefix = TAB_PREFIX.search(texts[ROUTE_KEYS])
    tabs = DEMOGRAPHICS_TABS.search(texts[ROUTE_KEYS])
    if texts[ROUTE_KEYS] and (prefix is None or tabs is None):
        problems.append(f"{ROUTE_KEYS}: PATIENT_TAB_PREFIX or DEMOGRAPHICS_TABS not found")
    if prefix and f'"{prefix.group(1)}" + tab.route' not in texts[PATIENT_ROUTES]:
        problems.append(
            f"{PATIENT_ROUTES}: patient tab keys `\"{prefix.group(1)}\" + tab.route` not found (routeKeys PATIENT_TAB_PREFIX)"
        )
    if tabs:
        for tab in re.findall(r'"([^"]+)"', tabs.group(1)):
            if f'route: "{tab}"' not in texts[PATIENT_TABS]:
                problems.append(
                    f"{PATIENT_TABS}: patient tab route \"{tab}\" not found (routeKeys DEMOGRAPHICS_TABS)"
                )

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
