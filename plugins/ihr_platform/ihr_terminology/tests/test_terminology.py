"""The terminology engine (P1-T10a1, ADR-016), tested through CARE's own classes and API, with no
mock of the terminology path: CARE's own tests patch ValueSet.search/lookup and turn coded-field
validation off for the whole process, so they never reach this code.

All content here is synthetic (made-up systems and codes), never SNOMED or LOINC.
"""

import json
from unittest import mock

import requests
from care.emr.fhir.client import FHIRClient
from care.emr.fhir.resources.base import ResourceManger
from care.emr.fhir.resources.code_concept import CodeConceptResource
from care.emr.models.valueset import ValueSet
from care.emr.registries.care_valueset import care_valueset
from care.emr.resources.common.coding import Coding
from care.users.models import User
from django.db import DatabaseError, connection, transaction
from django.test import SimpleTestCase, TestCase
from django_tenants.utils import schema_context
from ihr_identity import testing
from ihr_platform.models import Hospital
from ihr_platform.test_runner import TEST_SCHEMA

from ihr_terminology import client, ecl, engine, loader, ucum
from ihr_terminology.models import CodeSystemVersion, Concept, Designation
from ihr_terminology.text import normalise

TEST = "http://example.org/fhir/CodeSystem/ihr-test"
NUM = "http://example.org/fhir/CodeSystem/ihr-test-numeric"
UNLOADED = "http://example.org/fhir/CodeSystem/not-loaded"
SNOMED = "http://snomed.info/sct"


def _cs(url, version, concepts):
    return {
        "resourceType": "CodeSystem",
        "url": url,
        "version": version,
        "name": "Test",
        "content": "complete",
        "concept": concepts,
    }


TEST_CS = _cs(
    TEST,
    "1.0.0",
    [
        {
            "code": "R",
            "display": "Test root",
            "concept": [
                {
                    "code": "A",
                    "display": "Alpha finding",
                    "concept": [
                        {
                            "code": "A1",
                            "display": "Alpha finding, left",
                            "designation": [{"language": "hi", "value": "अल्फा बायां"}],
                        },
                        {"code": "C", "display": "Combined alpha beta"},
                    ],
                },
                {
                    "code": "B",
                    "display": "Beta disorder (disorder)",
                    "concept": [
                        {
                            "code": "C2",
                            "display": "Combined beta only",
                            "property": [{"code": "parent", "valueCode": "A"}],
                        }
                    ],
                },
                {
                    "code": "X",
                    "display": "Retired thing",
                    "property": [{"code": "inactive", "valueBoolean": True}],
                },
                {"code": "Z1", "display": "Gamma"},
                {
                    "code": "A9",
                    "display": "Gamma ray burst",
                    "designation": [
                        {
                            "language": "hi",
                            "value": "गामा किरण",
                            "use": {"code": "preferredForLanguage"},
                        }
                    ],
                },
                {"code": "%", "display": "percent sign"},
                {"code": "M1", "display": "Microgram dose µg"},
                *[
                    {"code": f"N{i:03d}", "display": f"Numbered item {i:03d}"}
                    for i in range(1, 121)
                ],
            ],
        },
    ],
)
NUM_CS = _cs(
    NUM,
    "2.0.0",
    [
        {
            "code": "100000",
            "display": "Numeric root",
            "concept": [
                {
                    "code": "100001",
                    "display": "Numeric child",
                    "concept": [{"code": "100002", "display": "Numeric grandchild"}],
                },
            ],
        },
    ],
)


def load(res, sha="0" * 64):
    with schema_context("public"):
        return loader.load_resource(
            res, source="test", sha256=sha, operator="test-runner"
        )


def vs(compose, slug="ihr-test-vs"):
    # CARE iterates compose.exclude without a None check: always give it a list
    return ValueSet(
        slug=slug, name=slug, status="active", compose={"exclude": [], **compose}
    )


def whole(system, **extra):
    return {"include": [{"system": system, **extra}], "exclude": []}


class Units(SimpleTestCase):
    def test_normalise(self):
        self.assertEqual(
            normalise("Diabetes mellitus (disorder)"), "diabetes mellitus disorder"
        )
        self.assertEqual(normalise("  Café,  NÉE "), "cafe nee")
        self.assertEqual(normalise("अल्फा बायां"), "अल्फा बायां")
        self.assertEqual(
            normalise("தமிழ் சொல்"), "தமிழ் சொல்"
        )  # other Indian scripts keep their marks

    def test_display_comparison(self):
        from ihr_terminology.text import same_display

        self.assertTrue(same_display("Alpha  finding, LEFT", "alpha finding left"))
        for a, b in (("µg", "g"), ("°C", "C"), ("#", "தமிழ் ☃"), ("%", "‰"), ("", "")):
            with self.subTest(a=a, b=b):
                self.assertFalse(same_display(a, b))
        self.assertTrue(same_display("#", "#"))

    def test_ecl_subset(self):
        self.assertEqual(
            ecl.parse("<< 763158003 |Medicinal product|"), ("<<", "763158003")
        )
        self.assertEqual(ecl.parse("< 9265001"), ("<", "9265001"))
        self.assertEqual(ecl.parse("*"), ("", "*"))
        for bad in (
            "<< 404684003 : 363698007 = << 39057004",
            "<< 1234567 OR << 7654321",
            "^ 123456",
            '<< 404684003 {{ term = "x" }}',
            "<<",
            "MINUS 123456",
            "<< *",
        ):
            with self.subTest(bad=bad), self.assertRaises(ecl.EclError):
                ecl.parse(bad)

    def test_ucum_syntax(self):
        atoms = {"mg", "kg", "d", "min", "m2", "L", "mL", "%", "[iU]"}
        for good in (
            "{capsule}",
            "{breaths}/min",
            "mg/kg/d",
            "mg.kg-1",
            "m2",
            "10*3/mL",
            "/min",
            "[iU]/L",
            "mg{total}",
        ):
            with self.subTest(good=good):
                self.assertTrue(ucum.is_valid(good, atoms))
        for bad in (
            "notaunit",
            "mg kg",
            "(mg)/d",
            "{a{b}}",
            "mg//d",
            "",
            "mg/",
            "x" * 200,
            "mg/१२",
            "mg٣",
        ):
            with self.subTest(bad=bad):
                self.assertFalse(ucum.is_valid(bad, atoms))


class Engine(TestCase):
    @classmethod
    def setUpTestData(cls):
        load(TEST_CS)
        load(NUM_CS)

    def test_whole_system_search_and_ranking(self):
        results = vs(whole(TEST)).search(search="alpha", count=10)
        codes = [r.code for r in results]
        self.assertEqual(
            codes[:2], ["A", "A1"]
        )  # starts with the words, shortest first
        self.assertIn("C", codes)
        self.assertNotIn(
            "X", [r.code for r in vs(whole(TEST)).search(search="retired")]
        )  # inactive never offered
        # every word must match, in any order, by word prefix
        self.assertEqual(
            [r.code for r in vs(whole(TEST)).search(search="beta comb")], ["C2", "C"]
        )  # shorter first
        # CARE's medication picker adds " clinical drug": a tag in brackets is searchable
        self.assertEqual(
            [r.code for r in vs(whole(TEST)).search(search="beta disorder")], ["B"]
        )
        # the exact term beats a longer one whose code sorts first
        self.assertEqual(
            [r.code for r in vs(whole(TEST)).search(search="gamma")], ["Z1", "A9"]
        )
        # the code as typed ranks first, also when the text has no words ("%")
        self.assertEqual(vs(whole(TEST)).search(search="A1")[0].code, "A1")
        self.assertEqual(vs(whole(TEST)).search(search="a1")[0].code, "A1")
        self.assertEqual(vs(whole(TEST)).search(search="%")[0].code, "%")
        self.assertEqual(vs(whole(TEST)).search(search="N007")[0].code, "N007")

    def test_count_capped_and_empty_search(self):
        self.assertEqual(len(vs(whole(TEST)).search(search="", count=99)), 99)
        r = client.LOCAL.query(
            method="POST",
            resource="ValueSet/$expand",
            parameters={
                "parameter": [
                    {"name": "count", "valueInteger": 100000},
                    {
                        "name": "valueSet",
                        "resource": {
                            "resourceType": "ValueSet",
                            "compose": whole(TEST),
                        },
                    },
                ]
            },
        )
        self.assertEqual(len(r["expansion"]["contains"]), engine.MAX_COUNT)

    def test_the_typed_code_is_found_by_index(self):
        """expand() looks up the code as typed in any case before the word search; at SNOMED
        scale that took 171 ms without this index (bench run 3)."""
        with schema_context("public"), connection.cursor() as cur:
            found = connection.introspection.get_constraints(
                cur, Concept._meta.db_table
            )
        self.assertIn("ihr_term_code_upper", found)
        self.assertTrue(found["ihr_term_code_upper"]["index"])
        self.assertEqual(vs(whole(TEST)).search(search="n007")[0].code, "N007")

    def test_candidates_chosen_in_sql_by_the_same_keys(self):
        """With words, at most MAX_CANDIDATES concepts are read; the SQL must pick them by the
        leading ranking keys, or a better match past the cap would be lost (review F6). The texts
        differ from other tests' as typed: CARE caches identical searches for 10 s."""
        with mock.patch.object(engine, "MAX_CANDIDATES", 1):
            # "Gamma" (exact) and "Gamma ray burst": the exact term must be the one candidate
            self.assertEqual(
                [r.code for r in vs(whole(TEST)).search(search="GAMMA")], ["Z1"]
            )
            # "Combined beta only" is shorter than "Combined alpha beta": both match every word
            self.assertEqual(
                [r.code for r in vs(whole(TEST)).search(search="Comb Beta")], ["C2"]
            )
            # a term starting with the words beats a shorter one that only contains them
            self.assertEqual(
                [r.code for r in vs(whole(TEST)).search(search="ALPHA")], ["A"]
            )
        with mock.patch.object(engine, "MAX_CANDIDATES", 3):
            got = [
                r.code for r in vs(whole(TEST)).search(search="Numbered Item", count=10)
            ]
            self.assertEqual(
                len(got), 3
            )  # the cap holds, and the answer is not an error
            self.assertTrue(all(c.startswith("N") for c in got))

    def test_value_sets_that_select_nothing_answer_empty(self):
        is_a_nope = {"include": [{"system": TEST, "filter": [{"property": "concept", "op": "is-a",
                                                               "value": "NOPE"}]}]}  # fmt: skip
        everything_excluded = {
            "include": [{"system": TEST, "concept": [{"code": "A"}]}],
            "exclude": [{"system": TEST, "concept": [{"code": "A"}]}],
        }
        for compose in (is_a_nope, everything_excluded):
            for search in ("", "alpha", "a"):
                self.assertEqual(
                    list(vs(compose).search(search=search)), [], (compose, search)
                )

    def test_marks_the_parser_drops_never_break_a_search(self):
        """Postgres's parser drops some characters (lone marks, punctuation) from both the
        designations and the search: the words left still match, and only-marks searches answer
        without error."""
        for search in ("alpha -", "alpha ,", "- alpha", "alpha / left", "alpha 'left"):
            self.assertIn(
                "A1",
                [r.code for r in vs(whole(TEST)).search(search=search, count=20)],
                search,
            )
        for search in ("-", "/", "'", "' & |", "\\"):
            vs(whole(TEST)).search(
                search=search
            )  # answers (codes A-Z), never a tsquery error
        self.assertEqual(
            [r.code for r in vs(whole(TEST)).search(search="µg dose")], ["M1"]
        )

    def test_hierarchy_filters(self):
        def codes(*filters, system=TEST):
            return sorted(
                r.code
                for r in vs(
                    {"include": [{"system": system, "filter": list(filters)}]}
                ).search(count=100)
                if not r.code.startswith("N")
            )

        self.assertEqual(
            codes({"property": "concept", "op": "is-a", "value": "A"}),
            ["A", "A1", "C", "C2"],
        )
        self.assertEqual(
            codes({"property": "concept", "op": "descendent-of", "value": "A"}),
            ["A1", "C", "C2"],
        )
        self.assertEqual(codes({"property": "concept", "op": "=", "value": "B"}), ["B"])
        self.assertEqual(
            codes({"property": "concept", "op": "in", "value": "A,B"}), ["A", "B"]
        )
        self.assertEqual(
            codes({"property": "concept", "op": "is-a", "value": "NOPE"}), []
        )
        self.assertEqual(
            codes(
                {
                    "property": "constraint",
                    "op": "=",
                    "value": "<< 100001 |Numeric child|",
                },
                system=NUM,
            ),
            ["100001", "100002"],
        )
        self.assertEqual(
            codes(
                {"property": "constraint", "op": "=", "value": "< 100001"}, system=NUM
            ),
            ["100002"],
        )
        self.assertEqual(
            codes(
                {"property": "constraint", "op": "=", "value": "> 100002"}, system=NUM
            ),
            ["100000", "100001"],
        )
        self.assertEqual(
            codes(
                {"property": "constraint", "op": "=", "value": ">> 100001"}, system=NUM
            ),
            ["100000", "100001"],
        )
        self.assertEqual(
            codes(
                {
                    "property": "constraint",
                    "op": "=",
                    "value": "100002 |Numeric grandchild|",
                },
                system=NUM,
            ),
            ["100002"],
        )
        self.assertEqual(
            codes({"property": "constraint", "op": "=", "value": "*"}, system=NUM),
            ["100000", "100001", "100002"],
        )

    def test_excludes(self):
        compose = {
            "include": [
                {
                    "system": TEST,
                    "filter": [{"property": "concept", "op": "is-a", "value": "A"}],
                }
            ],
            "exclude": [{"system": TEST, "concept": [{"code": "C"}]}],
        }
        self.assertEqual(
            sorted(r.code for r in vs(compose).search(count=100)), ["A", "A1", "C2"]
        )
        self.assertFalse(vs(compose).lookup(Coding(system=TEST, code="C")))
        self.assertTrue(vs(compose).lookup(Coding(system=TEST, code="C2")))

    def test_unsupported_compose_is_refused_never_widened(self):
        for f in (
            {"property": "concept", "op": "is-not-a", "value": "A"},
            {"property": "concept", "op": "regex", "value": ".*"},
            {"property": "inactive", "op": "=", "value": "false"},
            {"property": "concept", "op": "generalizes", "value": "A"},
            {"property": "constraint", "op": "=", "value": "<< 100001 : 1 = 2"},
        ):
            for part in ("include", "exclude"):
                compose = {"include": [{"system": TEST}], "exclude": []}
                compose[part] = [{"system": TEST, "filter": [f]}]
                with self.subTest(filter=f, part=part):
                    self.assertEqual(vs(compose).search(search="alpha"), [])
                    with self.assertRaises(
                        ValueError
                    ):  # CARE: "Valueset does not have specified code"
                        vs(compose).lookup(Coding(system=TEST, code="A"))

    def test_validate_code_rules(self):
        v = vs(whole(TEST))
        self.assertTrue(
            v.lookup(Coding(system=TEST, code="N120"))
        )  # beyond the first 100 codes
        self.assertTrue(
            v.lookup(Coding(system=TEST, code="A1", display="alpha FINDING, left"))
        )
        self.assertTrue(v.lookup(Coding(system=TEST, code="A1", display="अल्फा बायां")))
        self.assertFalse(
            v.lookup(Coding(system=TEST, code="M1", display="Microgram dose g"))
        )  # µ counts
        self.assertFalse(
            v.lookup(Coding(system=TEST, code="A1", display="Beta disorder"))
        )  # another term
        self.assertFalse(v.lookup(Coding(code="A1")))  # no system
        self.assertFalse(v.lookup(Coding(system=NUM, code="A1")))  # wrong system
        self.assertFalse(v.lookup(Coding(system=TEST, code="X")))  # inactive
        self.assertFalse(v.lookup(Coding(system=TEST, code="ZZZ")))

    def test_pinned_version_answers_from_the_active_one(self):
        """CARE pins HL7 code systems at 2.0.0; HL7 has moved on."""
        v = vs(whole(TEST, version="0.9.0"))
        self.assertTrue(v.search(search="alpha"))
        self.assertTrue(v.lookup(Coding(system=TEST, code="A")))

    def test_licence_gated_systems_answer_nothing_until_loaded(self):
        compose = {
            "include": [
                {
                    "system": SNOMED,
                    "concept": [
                        {"code": "123456", "display": "Listed in CARE's source"}
                    ],
                }
            ]
        }
        self.assertEqual(vs(compose).search(search="listed"), [])
        self.assertFalse(vs(compose).lookup(Coding(system=SNOMED, code="123456")))
        self.assertEqual(
            vs(
                whole(
                    SNOMED,
                    filter=[
                        {"property": "concept", "op": "is-a", "value": "404684003"}
                    ],
                )
            ).search(),
            [],
        )
        for gated in (
            "http://loinc.org",
            "http://hl7.org/fhir/sid/icd-10",
            "http://id.who.int/icd/release/11/mms",
        ):
            listed = {
                "include": [
                    {"system": gated, "concept": [{"code": "1-8", "display": "Listed"}]}
                ]
            }
            with self.subTest(gated=gated):
                self.assertEqual(vs(listed).search(), [])
                self.assertFalse(vs(listed).lookup(Coding(system=gated, code="1-8")))

    def test_unloaded_free_system_lists_its_own_codes(self):
        compose = {
            "include": [
                {
                    "system": UNLOADED,
                    "concept": [{"code": "k1", "display": "Kind one"}, {"code": "k2"}],
                }
            ]
        }
        found = vs(compose).search(search="")
        self.assertEqual(
            [(r.code, r.display) for r in found], [("k1", "Kind one"), ("k2", "k2")]
        )  # code-only: display = code
        self.assertTrue(vs(compose).lookup(Coding(system=UNLOADED, code="k2")))
        self.assertFalse(vs(compose).lookup(Coding(system=UNLOADED, code="k3")))
        self.assertEqual(
            vs(whole(UNLOADED)).search(), []
        )  # whole system, no content: nothing
        # its excludes and displays count too
        compose["exclude"] = [{"system": UNLOADED, "concept": [{"code": "k2"}]}]
        self.assertEqual([r.code for r in vs(compose).search()], ["k1"])
        self.assertFalse(vs(compose).lookup(Coding(system=UNLOADED, code="k2")))
        self.assertFalse(
            vs(compose).lookup(Coding(system=UNLOADED, code="k1", display="Kind two"))
        )
        self.assertTrue(
            vs(compose).lookup(Coding(system=UNLOADED, code="k1", display="kind ONE"))
        )

    def test_value_set_display_of_a_loaded_code(self):
        compose = {
            "include": [
                {
                    "system": TEST,
                    "concept": [
                        {"code": "A", "display": "Alpha (as the value set says)"}
                    ],
                }
            ]
        }
        self.assertEqual(
            vs(compose).search()[0].display, "Alpha (as the value set says)"
        )
        self.assertTrue(
            vs(compose).lookup(
                Coding(system=TEST, code="A", display="Alpha (as the value set says)")
            )
        )
        self.assertTrue(
            vs(compose).lookup(Coding(system=TEST, code="A", display="Alpha finding"))
        )

    def test_display_language(self):
        r = client.LOCAL.query(
            method="POST",
            resource="ValueSet/$expand",
            parameters={
                "parameter": [
                    {"name": "filter", "valueString": "left"},
                    {"name": "displayLanguage", "valueString": "hi"},
                    {"name": "includeDesignations", "valueBoolean": True},
                    {
                        "name": "valueSet",
                        "resource": {
                            "resourceType": "ValueSet",
                            "compose": whole(TEST),
                        },
                    },
                ]
            },
        )
        row = r["expansion"]["contains"][0]
        self.assertEqual(
            row["display"], "Alpha finding, left"
        )  # no preferred Hindi term for A1
        self.assertIn({"language": "hi", "value": "अल्फा बायां"}, row["designation"])
        r = client.LOCAL.query(
            method="POST",
            resource="ValueSet/$expand",
            parameters={
                "parameter": [
                    {"name": "filter", "valueString": "ray burst"},
                    {"name": "displayLanguage", "valueString": "hi"},
                    {
                        "name": "valueSet",
                        "resource": {
                            "resourceType": "ValueSet",
                            "compose": whole(TEST),
                        },
                    },
                ]
            },
        )
        self.assertEqual(
            r["expansion"]["contains"][0]["display"], "गामा किरण"
        )  # preferred in Hindi
        r = client.LOCAL.query(
            method="POST",
            resource="ValueSet/$expand",
            parameters={
                "parameter": [
                    {"name": "filter", "valueString": "ray burst"},
                    {"name": "displayLanguage", "valueString": "en-gb"},
                    {
                        "name": "valueSet",
                        "resource": {
                            "resourceType": "ValueSet",
                            "compose": whole(TEST),
                        },
                    },
                ]
            },
        )
        self.assertEqual(r["expansion"]["contains"][0]["display"], "Gamma ray burst")

    def test_lookup_through_care(self):
        out = CodeConceptResource().filter(system=TEST, code="C2", property="*").get()
        self.assertEqual(out["metadata"]["display"], "Combined beta only")
        self.assertEqual(sorted(out["properties"]["parents"]), ["A", "B"])
        self.assertIn(
            "N001",
            CodeConceptResource()
            .filter(system=TEST, code="R", property="*")
            .get()["properties"]["children"],
        )
        with self.assertRaises(ValueError):
            CodeConceptResource().filter(system=TEST, code="NOPE", property="*").get()
        self.assertTrue(
            CodeConceptResource()
            .filter(system=TEST, code="X", property="*")
            .get()["metadata"]["inactive"]
        )

    def test_care_coded_field_validation(self):
        """The real validator (CARE's test base replaces the module-level one with a no-op)."""
        ValueSet.objects.create(
            slug="ihr-test-codes", name="t", status="active", compose=whole(TEST)
        )
        coding = Coding(system=TEST, code="A1")
        self.assertEqual(
            care_valueset.validate_valueset("f", "ihr-test-codes", coding), coding
        )
        with self.assertRaises(ValueError):
            care_valueset.validate_valueset(
                "f", "ihr-test-codes", Coding(system=TEST, code="ZZZ")
            )

    def test_limits(self):
        def expand(compose, search=""):
            return client.LOCAL.query(
                method="POST",
                resource="ValueSet/$expand",
                parameters={
                    "parameter": [
                        {"name": "filter", "valueString": search},
                        {
                            "name": "valueSet",
                            "resource": {
                                "resourceType": "ValueSet",
                                "compose": compose,
                            },
                        },
                    ]
                },
            )

        self.assertTrue(
            expand(whole(TEST), "num " * 12)["expansion"]["contains"]
        )  # at the limits: answered
        for over in (
            "alpha" + " " * 100,
            "num " * 13,
        ):  # just over: refused, though they would match
            with self.subTest(over=over[:20]):
                self.assertEqual(
                    expand(whole(TEST), over)["resourceType"], "OperationOutcome"
                )
        self.assertEqual(
            expand(
                {"include": [{"system": TEST} for _ in range(engine.MAX_INCLUDES + 1)]}
            )["resourceType"],
            "OperationOutcome",
        )
        listed = {
            "include": [
                {
                    "system": TEST,
                    "concept": [
                        {"code": f"N{i}"} for i in range(engine.MAX_CONCEPTS + 1)
                    ],
                }
            ]
        }
        self.assertEqual(expand(listed)["resourceType"], "OperationOutcome")

    def test_search_budget_per_request_is_an_error_not_an_answer(self):
        token = engine.start_request()
        try:
            for i in range(
                engine.MAX_SEARCHES_PER_REQUEST
            ):  # different texts: CARE caches identical ones 10 s
                vs(whole(TEST)).search(search=f"numbered {i:03d}")
            with self.assertRaises(engine.TooManySearches):
                vs(whole(TEST)).search(search="alpha left")
            # code checks are point queries: never budgeted, so long forms still save
            for i in range(engine.MAX_SEARCHES_PER_REQUEST + 5):
                self.assertTrue(
                    vs(whole(TEST)).lookup(Coding(system=TEST, code=f"N{i + 1:03d}"))
                )
        finally:
            engine.end_request(token)

    def test_statement_timeout_set_and_restored(self):
        def setting():
            with connection.cursor() as cur:
                cur.execute("SELECT current_setting('statement_timeout')")
                return cur.fetchone()[0]

        before = setting()
        with engine._Timeout():
            self.assertEqual(
                setting(), "2s"
            )  # STATEMENT_TIMEOUT_MS, as Postgres shows it
        self.assertEqual(setting(), before)
        with (
            self.assertRaises(engine.TooSlow),
            engine._Timeout(),
            connection.cursor() as cur,
        ):
            cur.execute("SELECT set_config('statement_timeout', '50', true)")
            cur.execute("SELECT pg_sleep(1)")
        self.assertEqual(setting(), before)  # and the outer transaction still works

    def test_code_system_search(self):
        bundle = client.LOCAL.query(
            method="GET", resource="CodeSystem", parameters={"url": TEST}
        )
        self.assertEqual([e["resource"]["url"] for e in bundle["entry"]], [TEST])
        outcome = client.LOCAL.query(method="GET", resource="ValueSet", parameters={})
        self.assertEqual(outcome["resourceType"], "OperationOutcome")


class NothingLeaves(TestCase):
    def test_care_client_is_ours_and_the_remote_one_refuses(self):
        self.assertIs(ResourceManger._fhir_client, client.LOCAL)
        with (
            mock.patch.object(
                requests, "request", side_effect=AssertionError("would have gone out")
            ) as sent,
            self.assertRaises(RuntimeError),
        ):
            FHIRClient("http://192.0.2.1/fhir").query(
                method="GET", resource="CodeSystem", parameters={}
            )
        sent.assert_not_called()

    def test_no_http_during_terminology_calls(self):
        load(TEST_CS)
        with (
            mock.patch.object(
                requests.Session, "request", side_effect=AssertionError("network")
            ),
            mock.patch.object(
                requests, "request", side_effect=AssertionError("network")
            ),
        ):
            self.assertTrue(vs(whole(TEST)).search(search="alpha"))
            self.assertTrue(vs(whole(TEST)).lookup(Coding(system=TEST, code="A")))
            CodeConceptResource().filter(system=TEST, code="A", property="*").get()


class ReadOnly(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.version = load(TEST_CS)

    def _refused(self, fn):
        with self.assertRaises(DatabaseError), transaction.atomic():
            fn()

    def test_hospital_context_cannot_write(self):
        connection.set_schema(TEST_SCHEMA)
        concept = Concept.objects.filter(system=self.version).first()
        self._refused(
            lambda: Concept.objects.create(system=self.version, code="NEW", display="x")
        )
        self._refused(
            lambda: Concept.objects.filter(pk=concept.pk).update(display="changed")
        )
        self._refused(lambda: Designation.objects.filter(concept=concept).delete())
        self._refused(
            lambda: CodeSystemVersion.objects.filter(pk=self.version.pk).update(
                status="retired"
            )
        )
        self._refused(
            lambda: Concept.objects.bulk_create(
                [Concept(system=self.version, code="NEW2", display="x")]
            )
        )

        def raw():
            with connection.cursor() as cur:
                cur.execute("UPDATE public.ihr_terminology_concept SET display = 'x'")

        self._refused(raw)
        # ...and the loader itself refuses a hospital's context
        with self.assertRaises(loader.LoadError):
            loader.load_resource(NUM_CS, source="t", sha256="1" * 64, operator="x")


class Loader(TestCase):
    def test_versions_retire_and_identical_loads_are_skipped(self):
        v1 = load(TEST_CS, sha="a" * 64)
        self.assertEqual(
            load(TEST_CS, sha="a" * 64).pk, v1.pk
        )  # same content: nothing new
        v2 = load({**TEST_CS, "version": "1.1.0"}, sha="b" * 64)
        v1.refresh_from_db()
        self.assertEqual((v1.status, v2.status), ("retired", "active"))
        self.assertEqual(v2.loaded_by, "test-runner")
        with schema_context("public"):
            loader.prune(operator="test-runner")
        self.assertFalse(Concept.objects.filter(system=v1).exists())
        self.assertTrue(
            CodeSystemVersion.objects.filter(pk=v1.pk).exists()
        )  # the history stays
        self.assertEqual(
            Concept.objects.filter(system=v2).count(), v2.concepts
        )  # the active one is whole
        self.assertTrue(vs(whole(TEST)).lookup(Coding(system=TEST, code="A")))

    def test_answers_come_from_the_active_version(self):
        load(TEST_CS, sha="e" * 64)
        changed = json.loads(
            json.dumps(TEST_CS).replace('"Alpha finding"', '"Alpha finding, revised"')
        )
        load({**changed, "version": "1.2.0"}, sha="f" * 64)
        self.assertEqual(
            vs(whole(TEST)).search(search="alpha revised")[0].display,
            "Alpha finding, revised",
        )

    def test_refusals(self):
        with schema_context("public"):
            with self.assertRaises(loader.LoadError):
                loader.load_resource(TEST_CS, source="t", sha256="c" * 64, operator="")
            cyclic = _cs(
                "http://example.org/cyclic",
                "1",
                [
                    {
                        "code": "P",
                        "display": "p",
                        "property": [{"code": "parent", "valueCode": "Q"}],
                    },
                    {
                        "code": "Q",
                        "display": "q",
                        "property": [{"code": "parent", "valueCode": "P"}],
                    },
                ],
            )
            with self.assertRaises(loader.LoadError):
                loader.load_resource(cyclic, source="t", sha256="d" * 64, operator="x")

    def test_sha256_checked(self):
        path = loader.CONTENT / "hl7-terminology-7.5.0" / "CodeSystem-v2-0916.json"
        with schema_context("public"), self.assertRaises(loader.LoadError):
            loader.load_file(path, sha256="0" * 64, operator="x")

    def test_bundled_content(self):
        manifest = json.loads((loader.CONTENT / "MANIFEST.json").read_text())
        with schema_context("public"):
            versions = loader.load_bundled(operator="test-runner")
        self.assertEqual(len(versions), len(manifest["files"]))
        self.assertTrue(all(new for _, new in versions))
        ucum_v = CodeSystemVersion.objects.get(url=engine.UCUM, status="active")
        self.assertEqual(
            (ucum_v.content, ucum_v.concepts), ("fragment", 1357)
        )  # 7 listed twice, merged
        # CARE's pinned HL7 value sets work (pinned at 2.0.0; THO has 3.0.0 and 1.1.1)
        specimen = vs(
            {
                "include": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/v2-0487",
                        "version": "2.0.0",
                    }
                ]
            }
        )
        self.assertTrue(
            specimen.lookup(
                Coding(
                    system="http://terminology.hl7.org/CodeSystem/v2-0487", code="BLD"
                )
            )
        )
        units = vs(whole(engine.UCUM))
        self.assertEqual(units.search(search="mg")[0].code, "mg")
        for unit in ("{capsule}", "{puff}", "{breaths}/min", "mg/kg/d"):
            self.assertTrue(units.lookup(Coding(system=engine.UCUM, code=unit)), unit)
        self.assertFalse(units.lookup(Coding(system=engine.UCUM, code="not a unit")))
        self.assertFalse(
            units.lookup(
                Coding(system=engine.UCUM, code="{breaths}/min", display="kilogram")
            )
        )
        self.assertFalse(units.lookup(Coding(system=engine.UCUM, code="mg/१२")))
        # a value set listing some units accepts those only
        some = vs({"include": [{"system": engine.UCUM, "concept": [{"code": "mg"}]}]})
        self.assertTrue(some.lookup(Coding(system=engine.UCUM, code="mg")))
        self.assertFalse(some.lookup(Coding(system=engine.UCUM, code="{capsule}")))
        # a newer version loaded by an operator is not replaced by the bundled one on the next `up`
        newer = {
            "resourceType": "CodeSystem",
            "url": "http://terminology.hl7.org/CodeSystem/v2-0916",
            "version": "9.9.9",
            "content": "complete",
            "concept": [{"code": "F", "display": "Patient was fasting"}],
        }
        load(newer, sha="9" * 64)
        with schema_context("public"):
            again = loader.load_bundled(operator="test-runner")
        self.assertFalse(any(new for _, new in again))
        self.assertEqual(
            CodeSystemVersion.objects.get(url=newer["url"], status="active").version,
            "9.9.9",
        )


class Api(TestCase):
    """CARE's own endpoints, signed in as a hospital's staff member, nothing mocked."""

    @classmethod
    def setUpTestData(cls):
        load(TEST_CS)
        ValueSet.objects.create(
            slug="ihr-api-vs",
            name="Test",
            status="active",
            compose=whole(TEST),
            description="",
        )
        cls.user = User.objects.create_user(
            username="term-reader", password="x-Unused-1"
        )

    def test_expand_validate_lookup(self):
        with schema_context("public"):
            hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        with testing.fake_keycloak():
            c = testing.staff_client(hospital, "testserver", self.user)
            expand = c.post(
                "/api/v1/valueset/ihr-api-vs/expand/",
                {"search": "alpha", "count": 5},
                format="json",
            )
            good = c.post(
                "/api/v1/valueset/ihr-api-vs/validate_code/",
                {"system": TEST, "code": "A1"},
                format="json",
            )
            bad = c.post(
                "/api/v1/valueset/ihr-api-vs/validate_code/",
                {"system": TEST, "code": "ZZ"},
                format="json",
            )
            found = c.post(
                "/api/v1/valueset/lookup_code/",
                {"system": TEST, "code": "A"},
                format="json",
            )
            missing = c.post(
                "/api/v1/valueset/lookup_code/",
                {"system": TEST, "code": "ZZ"},
                format="json",
            )
        self.assertEqual(expand.status_code, 200, expand.content[:300])
        self.assertEqual([r["code"] for r in expand.json()["results"]][:2], ["A", "A1"])
        self.assertEqual(
            (good.json(), bad.json()), ({"result": True}, {"result": False})
        )
        self.assertEqual(found.json()["metadata"]["display"], "Alpha finding")
        self.assertEqual(missing.status_code, 404)

    def test_search_budget_through_the_api(self):
        """The middleware is wired: one preview_search over 31 code systems is refused with 429."""
        with schema_context("public"):
            hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        compose = {
            "include": [
                {"system": f"{UNLOADED}/{i}", "concept": [{"code": "k"}]}
                for i in range(engine.MAX_SEARCHES_PER_REQUEST + 1)
            ],
            "exclude": [],
        }
        body = {
            "slug": "ihr-preview",
            "name": "Preview",
            "description": "",
            "status": "active",
            "compose": compose,
        }
        with testing.fake_keycloak():
            c = testing.staff_client(hospital, "testserver", self.user)
            r = c.post("/api/v1/valueset/preview_search/?search=k", body, format="json")
        self.assertEqual(r.status_code, 429, r.content[:200])
