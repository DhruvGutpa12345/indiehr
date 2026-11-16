"""Tests for scripts/check-ihr-frontend.py (P1-T11c1). Run: python3 -m unittest scripts/test_check_ihr_frontend.py

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
        self.edit(
            cif.FACILITY_NAV,
            "url: `${baseUrl}/settings/tag_config`,",
            "url: `${baseUrl}/settings/tag_config`,\n},{name:'x',url: `${baseUrl}/settings/extra`,",
        )
        self.assertProblem(cif.FACILITY_NAV, "settings items")

    def test_template_and_encounter_class_items_are_anchored(self):
        self.edit(cif.FACILITY_NAV, "url: `${baseUrl}/template`", "url: `${baseUrl}/templates`")
        self.assertProblem(cif.FACILITY_NAV, "Templates")
        self.edit(cif.FACILITY_NAV, "/encounters/patients/${encounterClass}", "/encounters/class/${encounterClass}")
        self.assertProblem(cif.FACILITY_NAV, "encounter class")

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

    # --- route keys -------------------------------------------------------------------------

    def test_encounter_route_template_change_fails(self):
        self.edit(
            cif.CONSULTATION_ROUTES,
            "/:id/patient/:patientId/encounter/:encounterId/:tab`",
            "/:id/patient/:patientId/encounter/:encounterId/:section`",
        )
        self.assertProblem(cif.CONSULTATION_ROUTES, "encounter route")

    def test_patient_route_key_change_fails(self):
        self.edit(
            cif.PATIENT_ROUTES,
            '"/facility/:facilityId/encounters/:tab"',
            '"/facility/:facilityId/encounters/:view"',
        )
        self.assertProblem(cif.PATIENT_ROUTES, "/facility/:facilityId/encounters/:tab")

    def test_each_wrapped_key_is_anchored(self):
        for key in (
            "/facility/:facilityId/encounters/locations/:locationId",
            "/facility/:facilityId/patient/:id",
            "/facility/:facilityId/patients",
        ):
            with self.subTest(key=key):
                self.setUp()
                self.edit(cif.PATIENT_ROUTES, f'"{key}"', f'"{key}x"', count=1)
                self.assertProblem(cif.PATIENT_ROUTES, key)

    def test_encounter_key_constant_must_exist(self):
        self.edit(cif.ROUTE_KEYS, "export const ENCOUNTER_KEY =", "const ENCOUNTER_KEY_OLD =")
        self.assertProblem(cif.ROUTE_KEYS, "ENCOUNTER_KEY")

    def test_gated_keys_are_anchored_in_their_own_table(self):
        # C1-1/F1: the encounter sub-pages' anchor; C1-2: the demographics allow-list; C1-5: lists
        for rel, key in (
            (cif.CONSULTATION_ROUTES, "/facility/:facilityId/patient/:patientId/encounter/:encounterId/questionnaire"),
            (cif.SCHEDULE_ROUTES, "/facility/:facilityId/patient/:patientId/appointments/:appointmentId"),
            (cif.PATIENT_ROUTES, "/facility/:facilityId/patient/:id/update"),
            (cif.PATIENT_ROUTES, "/facility/:facilityId/patient/create"),
            (cif.PATIENT_ROUTES, "/facility/:facilityId/patients/home"),
            (cif.PATIENT_ROUTES, "/facility/:facilityId/encounters/patients/all"),
            (cif.PATIENT_ROUTES, "/facility/:facilityId/encounters/patients/:encounterClass"),
        ):
            with self.subTest(key=key):
                self.setUp()
                self.edit(rel, f'"{key}"', f'"{key}x"', count=1)
                self.assertProblem(rel, key)

    def test_routed_constants_must_stay_defined(self):
        self.edit(cif.ROUTE_KEYS, "export const APPOINTMENT_KEY =", "const APPOINTMENT_KEY =")
        self.assertProblem(cif.ROUTE_KEYS, "APPOINTMENT_KEY")

    def test_patient_tab_keys_and_demographics_tabs_are_anchored(self):
        self.edit(
            cif.PATIENT_ROUTES,
            '"/facility/:facilityId/patient/:id/" + tab.route',
            '"/facility/:facilityId/patients/:id/" + tab.route',
        )
        self.assertProblem(cif.PATIENT_ROUTES, "patient tab keys")
        self.setUp()
        self.edit(cif.PATIENT_TABS, 'route: "accounts"', 'route: "billing"')
        self.assertProblem(cif.PATIENT_TABS, '"accounts"')

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

    def test_slug_copy_must_equal_the_backend_file_byte_for_byte(self):
        copy = self.root / cif.SLUGS_COPY
        copy.write_bytes(copy.read_bytes().rstrip(b"\n"))
        self.assertProblem(cif.SLUGS_COPY, "differs")

    def test_a_changed_slug_fails(self):
        self.edit(cif.SLUGS_SOURCE, '"Owner"', '"Owners"')
        self.assertProblem(cif.SLUGS_COPY, "differs")

    def test_missing_file_is_reported(self):
        (self.root / cif.PATIENT_ROUTES).unlink()
        self.assertProblem(cif.PATIENT_ROUTES, "missing")


if __name__ == "__main__":
    unittest.main()
