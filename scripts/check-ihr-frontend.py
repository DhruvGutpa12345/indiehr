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
   location-nav.tsx, overview.tsx);
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
APP_ROUTER = SRC / "Routers/AppRouter.tsx"
OVERVIEW = SRC / "pages/Facility/overview.tsx"
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
    APP_ROUTER,
    OVERVIEW,
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
}

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
