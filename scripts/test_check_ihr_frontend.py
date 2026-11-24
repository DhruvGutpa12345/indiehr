"""Tests for scripts/check-ihr-frontend.py (P1-T11c1; router anchors P2-T0c). Run: python3 -m unittest scripts/test_check_ihr_frontend.py

Each case copies the real files the check reads into a temporary tree, breaks one anchor, and
expects a problem naming that file; the untouched copy must pass.
"""

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check_ihr_frontend", Path(__file__).with_name("check-ihr-frontend.py")
)
cif = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cif)


class CheckIhrFrontend(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for rel in cif.FILES:
            dest = self.root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(cif.ROOT / rel, dest)
        for rel in cif.DIRS:
            shutil.copytree(cif.ROOT / rel, self.root / rel, dirs_exist_ok=True)

    def edit(self, rel, old, new, count=1):
        path = self.root / rel
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text, f"test setup: {old!r} not in {rel}")
        path.write_text(text.replace(old, new, count), encoding="utf-8")

    def problems(self):
        return cif.check(self.root)

    def assertProblem(self, rel, fragment=""):
        found = [p for p in self.problems() if str(rel) in p and fragment in p]
        self.assertTrue(found, f"no problem for {rel} {fragment!r}: {self.problems()}")

    # --- positive ---------------------------------------------------------------------------

    def test_real_files_pass(self):
        self.assertEqual(self.problems(), [])

    def test_real_repository_passes(self):
        self.assertEqual(cif.check(cif.ROOT), [])

    # --- menu suffixes ----------------------------------------------------------------------

    def test_renamed_facility_suffix_fails(self):
        self.edit(cif.FACILITY_NAV, "${baseUrl}/overview`", "${baseUrl}/home`")
        self.assertProblem(cif.FACILITY_NAV, "'/overview'")

    def test_renamed_child_suffix_fails(self):
        self.edit(cif.FACILITY_NAV, "${baseUrl}/billing/account`", "${baseUrl}/billing/accts`")
        self.assertProblem(cif.FACILITY_NAV, "/billing/account")

    def test_exact_suffix_is_not_satisfied_by_a_longer_one(self):
        # "/billing" must be a url of its own, not only the start of "/billing/account"
        self.edit(cif.FACILITY_NAV, "url: `${baseUrl}/billing`,", "url: `${baseUrl}/bills`,")
        self.assertProblem(cif.FACILITY_NAV, "'/billing'")

    def test_renamed_location_suffix_fails(self):
        self.edit(cif.LOCATION_NAV, "${baseUrl}/service_requests`", "${baseUrl}/requests`")
        self.assertProblem(cif.LOCATION_NAV, "/service_requests")

    def test_trailing_slash_suffix_fails_when_dropped(self):
        self.edit(
            cif.LOCATION_NAV,
            "${baseUrl}/inventory/internal/receive/`",
            "${baseUrl}/inventory/internal/receiving`",
        )
        self.assertProblem(cif.LOCATION_NAV, "/inventory/internal/receive/")

    def test_new_suffix_in_menu_rules_needs_an_upstream_anchor(self):
        self.edit(cif.MENU, 'suffix === "/resource"', 'suffix === "/resource" || suffix === "/nope"')
        self.assertProblem(cif.FACILITY_NAV, "/nope")

    def test_a_suffix_of_the_other_menu_does_not_count(self):
        # /beds exists only in the location menu: naming it in the facility rules fails
        self.edit(cif.MENU, 'suffix === "/resource"', 'suffix === "/beds"')
        self.assertProblem(cif.FACILITY_NAV, "/beds")

    def test_settings_count_changes_fail(self):
        # CARE 3.1: one childless Settings item; a second /settings/ url (children back) fails
        self.edit(
            cif.FACILITY_NAV,
            "url: `${baseUrl}/settings/general`,",
            "url: `${baseUrl}/settings/general`,\n},{name:'x',url: `${baseUrl}/settings/extra`,",
        )
        self.assertProblem(cif.FACILITY_NAV, "settings item")

    def test_encounters_item_is_anchored(self):
        self.edit(cif.FACILITY_NAV, "<EncounterNavItem", "<EncounterLink")
        self.assertProblem(cif.FACILITY_NAV, "Encounters item")

    def test_new_upstream_nav_item_must_be_classified(self):
        # F4: a new facility item, Patients child or location item is not "as CARE has it"
        # silently: it fails until menu.ts names it
        self.edit(
            cif.FACILITY_NAV,
            "url: `${baseUrl}/resource`,",
            "url: `${baseUrl}/resource`,\n},{name:'x',url: `${baseUrl}/reports`,",
        )
        self.assertProblem(cif.FACILITY_NAV, "${baseUrl}/reports is not classified")
        self.setUp()
        self.edit(
            cif.LOCATION_NAV,
            "url: `${baseUrl}/schedule`,",
            "url: `${baseUrl}/schedule`,\n},{name:'x',url: `${baseUrl}/radiology`,",
        )
        self.assertProblem(cif.LOCATION_NAV, "${baseUrl}/radiology is not classified")

    def test_prefix_literals_and_plugin_links_are_classified(self):
        self.assertTrue(cif._classified("/settings/new_thing", ["/settings/"]))
        self.assertTrue(cif._classified("/encounters/patients/", ["/encounters/patients/"]))
        self.assertFalse(cif._classified("/settingsx", ["/settings/"]))
        self.assertFalse(cif._classified("/billing/new", ["/billing", "/billing/account"]))

    def test_facility_base_url_is_anchored(self):
        # F2: ihrMenu.tsx rebuilds `/facility/${facilityId}`; upstream's base must stay the same
        self.edit(
            cif.FACILITY_NAV,
            "const baseUrl = `/facility/${selectedFacility.id}`;",
            "const baseUrl = `/facility/${selectedFacility.id}/`;",
        )
        self.assertProblem(cif.FACILITY_NAV, "const baseUrl")
        self.setUp()
        self.edit(cif.IHR_MENU, "`/facility/${facilityId}`", "`/f/${facilityId}`")
        self.assertProblem(cif.IHR_MENU, "facility base")

    def test_missing_section_markers_fail(self):
        self.edit(cif.MENU, cif.LOCATION_SECTION, "// Location menu")
        self.assertProblem(cif.MENU, "section markers")

    # --- route ids and the router hook points (P2-T0c) -----------------------------------

    def test_a_renamed_upstream_route_id_fails(self):
        # the gates name route ids; an upstream rename must fail before the gate goes quiet
        self.edit(
            cif.ROUTE_TREE,
            '"/_app/facility/$facilityId/encounters/locations/$locationId": typeof',
            '"/_app/facility/$facilityId/encounters/beds/$locationId": typeof',
        )
        self.assertProblem(cif.ROUTE_TREE, "encounters/locations/$locationId")

    def test_each_named_route_id_is_anchored(self):
        names = {name for name, _ in cif.ROUTE_ID_CONST.findall((cif.ROOT / cif.ROUTE_GATES).read_text())}
        self.assertGreaterEqual(len(names), 10)
        for name in ("ENCOUNTER_ID", "PATIENT_TAB_ID", "APPOINTMENT_ID", "PATIENT_SEARCH_ID", "ORG_ENCOUNTER_ID"):
            self.assertIn(name, names)

    def test_encounter_id_constant_must_exist(self):
        self.edit(cif.ROUTE_GATES, "export const ENCOUNTER_ID =", "const ENCOUNTER_ID_OLD =")
        self.assertProblem(cif.ROUTE_GATES, "ENCOUNTER_ID")

    def test_the_tree_block_must_exist(self):
        self.edit(cif.ROUTE_TREE, cif.TREE_BLOCK_START, "export interface RoutesById {")
        self.assertProblem(cif.ROUTE_TREE, "FileRoutesById")

    def test_demographics_tabs_are_anchored(self):
        self.edit(cif.PATIENT_TABS, 'route: "accounts"', 'route: "billing"')
        self.assertProblem(cif.PATIENT_TABS, '"accounts"')

    def test_lost_router_hook_marker_or_call_fails(self):
        for rel, call in cif.ROUTER_HOOKS.items():
            with self.subTest(rel=str(rel)):
                self.setUp()
                self.edit(rel, call, "x(", count=1)
                self.assertProblem(rel, "hook call")
                self.setUp()
                path = self.root / rel
                path.write_text(path.read_text().replace(cif.MARKER_ROUTER, "marker"), encoding="utf-8")
                self.assertProblem(rel, "marker")

    def test_the_staff_page_must_stay_inside_the_gate(self):
        # the page outside our gate (the sync kept the import but dropped the wrapper)
        self.edit(cif.APP_SHELL, "<IhrStaffPage>{children}</IhrStaffPage>", "{children}")
        self.assertProblem(cif.APP_SHELL, "inside our gate")
        # the page rendered a second time, outside the gate
        self.setUp()
        self.edit(cif.APP_SHELL, "</ErrorBoundary>", "</ErrorBoundary>\n{children}")
        self.assertProblem(cif.APP_SHELL, "rendered once only")
        # the gate moved outside CARE's ErrorBoundary
        self.setUp()
        self.edit(
            cif.APP_SHELL,
            "<IhrStaffPage>{children}</IhrStaffPage>\n            </ErrorBoundary>",
            "{children}\n            </ErrorBoundary>\n<IhrStaffPage>{children}</IhrStaffPage>",
        )
        self.assertProblem(cif.APP_SHELL, "ErrorBoundary")
        self.setUp()
        self.edit(cif.APP_SHELL, 'import { IhrStaffPage } from "@/components/ihr/IhrStaffPage";', "")
        self.assertProblem(cif.APP_SHELL, "marked import")

    def test_the_route_tree_call_and_import_are_anchored(self):
        self.edit(cif.ROUTER, 'import { withIhrRouteTree } from "@/components/ihr/ihrRouteTree";', "")
        self.assertProblem(cif.ROUTER, "import of our route tree")

    def test_the_watcher_encloses_the_gate_with_the_leaf_facility(self):
        self.edit(cif.IHR_STAFF_PAGE, "<IhrRouteGate", "<Fragment", count=1)
        self.assertProblem(cif.IHR_STAFF_PAGE, "grant watcher around the gate")
        self.setUp()
        self.edit(cif.IHR_STAFF_PAGE, "<IhrGrantWatcher", "<Fragment")
        self.assertProblem(cif.IHR_STAFF_PAGE, "grant watcher around the gate")
        self.setUp()
        self.edit(cif.IHR_STAFF_PAGE, "facilityId={leaf.facilityId}", "facilityId={undefined}")
        self.assertProblem(cif.IHR_STAFF_PAGE, "leaf's facility")
        self.setUp()
        self.edit(cif.IHR_STAFF_PAGE, "select: leafSignature", "select: (m) => m")
        self.assertProblem(cif.IHR_STAFF_PAGE, "committed matches")

    def test_the_auth_page_redirects_are_anchored(self):
        for old, fragment in (
            ('throw redirect({ to: "/login"', "redirect to /login"),
            ('from "@/routes/_public/2fa"', "2FA"),
            ('from "@/routes/_public/forgot-password"', "forgot-password"),
            ('from "@/routes/_public/password_reset.$token"', "password-reset"),
            ("getParentRoute: () => AppRoute", "under /_app"),
        ):
            with self.subTest(old=old):
                self.setUp()
                self.edit(cif.IHR_ROUTE_TREE, old, "SOMETHING_ELSE")
                self.assertProblem(cif.IHR_ROUTE_TREE, fragment)

    def test_the_login_page_keeps_our_sign_in_and_not_the_password_form(self):
        self.edit(cif.LOGIN, "<IhrStaffSignIn />", "<StaffLoginForm />")
        self.assertProblem(cif.LOGIN, "Keycloak sign-in")
        self.assertProblem(cif.LOGIN, "password form")
        self.setUp()
        self.edit(cif.LOGIN, "<IhrStaffSignIn />", "<IhrStaffSignIn /><ForgotPassword />")
        self.assertProblem(cif.LOGIN, "forgot-password form")

    def test_the_upstream_text_the_gates_rely_on_is_anchored(self):
        for rel, old, fragment in (
            (cif.APP_AREA, "beforeLoad: requireStaff", "staff area guard"),
            (cif.PUBLIC_AREA, "beforeLoad: publicOnly", "public area guard"),
            (cif.PATIENT_AREA, "beforeLoad: requirePatient", "patient area guard"),
            (cif.ROOT_ROUTE, "<AreaChrome", "chrome"),
            (cif.AREA_CHROME, "chrome = <AppShell>", "AppShell"),
            (cif.AREA_CHROME, "isAreaAllowed(", "area's session"),
            (cif.ROUTER_AUTH, 'if (area === "app") return status === "user";', "signed-in staff user"),
            (cif.CURRENT_FACILITY, "usePathname()", "address-bar facility the gate compares"),
            (cif.CURRENT_FACILITY, 'segments[1] === "facility"', "facilityFromPath"),
        ):
            with self.subTest(rel=str(rel), old=old):
                self.setUp()
                self.edit(rel, old, "SOMETHING_ELSE", count=100)
                self.assertProblem(rel, fragment)

    # --- no data fetch before the gate (design §6.3, attack S3) ----------------------------

    FETCHES = (
        ("loader: ", "  loader: () => fetchThing(),\n"),
        ("loader()", "  async loader() { return 1; },\n"),
        ("loaderDeps", "  loaderDeps: ({ search }) => search,\n"),
        ("context.queryClient", "  beforeLoad: ({ context }) => context.queryClient.clear(),\n"),
        ("ensureQueryData", "  beforeLoad: () => qc.ensureQueryData(opts),\n"),
        ("prefetchQuery", "  beforeLoad: () => qc.prefetchQuery(opts),\n"),
        ("prefetchInfiniteQuery", "  beforeLoad: () => qc.prefetchInfiniteQuery(opts),\n"),
        ("fetchQuery", "  beforeLoad: () => qc.fetchQuery(opts),\n"),
        # P2-T0c review F2: this codebase's own fetch helpers and any awaited beforeLoad
        ("callApi", "  beforeLoad: ({ params }) => { callApi(patientApi.get, {}); },\n"),
        ("query()", "  beforeLoad: () => { query(patientApi.get)(); },\n"),
        ("query.debounced", "  beforeLoad: () => { query.debounced(patientApi.get)(); },\n"),
        ("fetch", "  beforeLoad: () => { fetch('/api/v1/patient/x/'); },\n"),
        ("request", "  beforeLoad: () => { request('/api/v1/patient/x/'); },\n"),
        ("async beforeLoad", "  beforeLoad: async () => { guard(); },\n"),
        ("await", "  beforeLoad: () => wait(async () => { await thing(); }),\n"),
    )

    def _route_file(self):
        return cif.ROUTES_DIR / "_app" / "facility.$facilityId.overview.tsx"

    def test_a_route_file_that_fetches_fails(self):
        for name, line in self.FETCHES:
            with self.subTest(name=name):
                self.setUp()
                rel = self._route_file()
                self.edit(rel, "  component: RouteComponent,", line + "  component: RouteComponent,")
                found = [p for p in self.problems() if str(rel) in p and "before the IndieHR route gate" in p]
                self.assertTrue(found, f"{name}: {self.problems()}")

    def test_plugin_routes_that_fetch_fail(self):
        for name, line in self.FETCHES:
            with self.subTest(name=name):
                self.setUp()
                path = self.root / cif.PLUGIN_ROUTES
                path.write_text(path.read_text() + "\nconst x = {\n" + line + "};\n", encoding="utf-8")
                self.assertProblem(cif.PLUGIN_ROUTES, "before the IndieHR route gate")

    def test_a_new_nested_route_file_is_read_too(self):
        new = self.root / cif.ROUTES_DIR / "_app" / "x" / "deep.tsx"
        new.parent.mkdir(parents=True, exist_ok=True)
        new.write_text("export const Route = { loader: () => 1 };\n", encoding="utf-8")
        self.assertProblem(cif.ROUTES_DIR / "_app" / "x" / "deep.tsx", "route loader")

    # --- hooks ------------------------------------------------------------------------------

    def test_lost_hook_marker_or_call_fails(self):
        for rel, call in cif.HOOKS.items():
            with self.subTest(rel=str(rel)):
                self.setUp()
                self.edit(rel, call, "x(", count=1)
                self.assertProblem(rel, "hook call")
                self.setUp()
                path = self.root / rel
                path.write_text(path.read_text().replace(cif.MARKER, "marker"), encoding="utf-8")
                self.assertProblem(rel, "marker")

    # --- the front-desk and billing-desk screens (P1-T11c2) ----------------------------------

    def test_every_c2_screen_is_hooked(self):
        hooked = {
            cif.PATIENT_PROFILE,
            cif.DEMOGRAPHY,
            cif.PATIENT_REGISTRATION,
            cif.ENCOUNTER_HISTORY,
            cif.ENCOUNTER_CARD,
            cif.ENCOUNTER_ACTIONS,
            cif.PATIENT_HOME,
            cif.PATIENT_INFO_CARD,
            cif.CREATE_ENCOUNTER,
            cif.ENCOUNTER_LIST,
            cif.ACCOUNT_SHOW,
        }
        self.assertLessEqual(hooked, set(cif.HOOKS))

    def test_plugin_slot_key_change_fails(self):
        self.edit(
            cif.DEMOGRAPHY,
            'key="patient_details_tab__demography__general_info"',
            'key="patient_details_tab__demography__general"',
        )
        self.assertProblem(cif.DEMOGRAPHY, "plugin slot")

    def test_demography_section_ids_are_anchored(self):
        for section in ("additional-details", "tags", "general-info"):
            with self.subTest(section=section):
                self.setUp()
                self.edit(cif.DEMOGRAPHY, f'id: "{section}"', f'id: "{section}x"')
                self.assertProblem(cif.DEMOGRAPHY, section)

    def test_base_patient_tabs_export_is_anchored(self):
        self.edit(cif.PATIENT_TABS, "export const BASE_PATIENT_TABS", "const BASE_PATIENT_TABS")
        self.assertProblem(cif.PATIENT_TABS, "BASE_PATIENT_TABS")

    def test_encounter_list_filter_keys_are_anchored_even_when_split_over_lines(self):
        for call, key in (
            ("departmentFilter", "organization"),
            ("careTeamFilter", "care_team"),
            ("tagFilter", "tags"),
        ):
            with self.subTest(call=call):
                self.setUp()
                self.edit(cif.ENCOUNTER_LIST, f'{call}(', f'{call}Renamed(')
                self.assertProblem(cif.ENCOUNTER_LIST, key)

    def test_session_restored_filters_are_anchored(self):
        self.edit(cif.ENCOUNTER_LIST, "restoredParams.care_team_user", "restoredParams.care_team_member")
        self.assertProblem(cif.ENCOUNTER_LIST, "care_team_user")

    def test_a_new_account_tab_must_be_classified(self):
        self.edit(cif.ACCOUNT_SHOW, '  "bed_charge_items",\n] as const', '  "bed_charge_items",\n  "statements",\n] as const')
        self.assertProblem(cif.ACCOUNT_SHOW, "ACCOUNT_TABS")

    def test_a_dropped_account_tab_must_be_classified(self):
        self.edit(cif.ACCOUNT_SHOW, '  "reports",\n', "")
        self.assertProblem(cif.ACCOUNT_SHOW, "ACCOUNT_TABS")

    def test_account_nav_tabs_wrapper_is_anchored(self):
        self.edit(cif.ACCOUNT_SHOW, "<IhrNavTabs", "<NavTabs")
        self.assertProblem(cif.ACCOUNT_SHOW, "NavTabs")

    def test_every_hook_site_is_anchored_not_only_one_per_file(self):
        # (file, text to break, a fragment the problem names); every site carries a leak or a dead end
        sites = [
            (cif.PATIENT_HOME, "hideTags={ihrHidesPatientTags(", "ihrHidesPatientTags"),
            (cif.PATIENT_HOME, "ihrCanListEncounters(", "ihrCanListEncounters"),
            (cif.ENCOUNTER_LIST, "ihrEncounterFilters(", "ihrEncounterFilters"),
            (cif.ENCOUNTER_LIST, "ihrEncounterListEnabled(", "ihrEncounterListEnabled"),
            (cif.PATIENT_REGISTRATION, "hideBloodGroup={ihrHidesClinical}", "hideBloodGroup"),
            (cif.PATIENT_REGISTRATION, "!ihrHidesClinical && extensions.fields", "extensions"),
            (cif.PATIENT_REGISTRATION, "hideDeceased={ihrHidesClinical}", "hideDeceased"),
            (cif.PATIENT_REGISTRATION, "!hideDeceased &&", "hideDeceased"),
            (cif.ACCOUNT_SHOW, "ihrAccountActions(ihr)", "ihrAccountActions"),
            (cif.CHARGE_ITEMS_TABLE, "{canEditChargeItems && (", "canEditChargeItems"),
            (cif.BED_CHARGE_ITEMS_TABLE, "{canEditChargeItems && (", "canEditChargeItems"),
        ]
        for rel, old, fragment in sites:
            with self.subTest(rel=str(rel), old=old):
                self.setUp()
                self.edit(rel, old, "SOMETHING_ELSE", count=100)
                self.assertProblem(rel, fragment)

    def test_a_lost_account_button_gate_is_caught_even_when_others_remain(self):
        # one of the two invoice buttons (desktop, mobile) loses its gate: the other still matches,
        # so the count is what catches it
        for gate, fragment in (
            ("ihrActions.invoice &&", "ihrActions\\.invoice"),
            ("ihrActions.payment &&", "ihrActions\\.payment"),
            ("ihrActions.account &&", "ihrActions\\.account"),
        ):
            with self.subTest(gate=gate):
                self.setUp()
                self.edit(cif.ACCOUNT_SHOW, gate, "true &&", count=1)
                self.assertProblem(cif.ACCOUNT_SHOW, fragment)

    def test_both_tables_must_receive_the_charge_item_edit_right(self):
        self.edit(cif.ACCOUNT_SHOW, "canEditChargeItems={ihrActions.chargeItems}", "", count=1)
        self.assertProblem(cif.ACCOUNT_SHOW, "canEditChargeItems")

    def test_the_bulk_select_stays_disabled_for_read_only_roles(self):
        self.edit(cif.CHARGE_ITEMS_TABLE, "disabled={!canEditChargeItems}", "", count=1)
        self.assertProblem(cif.CHARGE_ITEMS_TABLE, "disabled")

    def test_the_account_menu_condition_is_anchored(self):
        self.edit(cif.ACCOUNT_SHOW, "(ihrActions.account || ihrActions.payment)", "true")
        self.assertProblem(cif.ACCOUNT_SHOW, "found 0 time")

    # --- slug files -------------------------------------------------------------------------

    # --- records access and the grant watcher (P1-T11c3) -----------------------------------

    def test_records_banner_is_mounted_in_both_patient_screens(self):
        for rel in (cif.PATIENT_PROFILE, cif.PATIENT_HOME):
            with self.subTest(rel=str(rel)):
                self.setUp()
                self.edit(rel, "<IhrPatientActions", "<IhrNothing")
                self.assertProblem(rel, "IhrPatientActions")

    def test_records_banner_import_marker_is_anchored(self):
        self.edit(
            cif.PATIENT_HOME,
            'import { IhrPatientActions } from "@/components/ihr/IhrPatientActions"; // IndieHR P1-T11c',
            'import { IhrPatientActions } from "@/components/ihr/IhrPatientActions";',
        )
        self.assertProblem(cif.PATIENT_HOME, "marked import")

    def test_upstream_blocks_the_banner_sits_beside_are_anchored(self):
        self.edit(cif.PATIENT_PROFILE, "<PatientDeceasedInfo", "<PatientDeceased")
        self.assertProblem(cif.PATIENT_PROFILE, "PatientDeceasedInfo")
        self.setUp()
        self.edit(cif.PATIENT_HOME, "</PatientInfoCard>", "</PatientCard>")
        self.assertProblem(cif.PATIENT_HOME, "PatientInfoCard")

    def test_the_chart_tab_is_anchored(self):
        self.edit(cif.PATIENT_TABS, 'route: "clinical_history"', 'route: "history"')
        self.assertProblem(cif.PATIENT_TABS, "clinical_history")

    def test_the_hospital_switcher_mount_is_anchored(self):
        # P1-T31: a sync that drops the tag or its import from the staff user menu is caught
        self.edit(cif.NAV_USER, "<IhrHospitalsMenu />", "")
        self.assertProblem(cif.NAV_USER, "the Hospitals group in the user menu")
        self.setUp()
        self.edit(
            cif.NAV_USER,
            'import { IhrHospitalsMenu } from "@/components/ihr/IhrHospitalsMenu";',
            "",
        )
        self.assertProblem(cif.NAV_USER, "its import")

    def test_slug_copy_must_equal_the_backend_file_byte_for_byte(self):
        copy = self.root / cif.SLUGS_COPY
        copy.write_bytes(copy.read_bytes().rstrip(b"\n"))
        self.assertProblem(cif.SLUGS_COPY, "differs")

    def test_a_changed_slug_fails(self):
        self.edit(cif.SLUGS_SOURCE, '"Owner"', '"Owners"')
        self.assertProblem(cif.SLUGS_COPY, "differs")

    def test_missing_file_is_reported(self):
        (self.root / cif.ROUTE_TREE).unlink()
        self.assertProblem(cif.ROUTE_TREE, "missing")


if __name__ == "__main__":
    unittest.main()
