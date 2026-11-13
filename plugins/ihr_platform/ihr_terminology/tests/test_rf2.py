"""The RF2 loader (P1-T10a2) on a hand-made release: made-up ids and terms, RF2's layout, never
SNOMED content. tests/data/rf2/ holds seven concepts:

    900000101 Test root
    ├── 900000102 Zeta finding           (and an inactive is-a to the root, ignored)
    │     ├── 900000104 Zeta omega combined   (also under 900000103: two parents)
    │     │     └── 900000107 Paracetamol 500 mg tablet (+ a non-is-a relationship, ignored)
    │     └── 900000106 Fsn only thing     (no synonym: its display is the FSN)
    ├── 900000103 Omega disorder
    └── 900000105 Retired concept         (inactive)
"""

import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from care.emr.models.valueset import ValueSet
from care.emr.resources.common.coding import Coding
from django.test import TestCase
from django_tenants.utils import schema_context
from ihr_platform.testing import as_owner, owner_for_test

from ihr_terminology import engine, loader, rf2
from ihr_terminology.models import CodeSystemVersion, Concept, Designation

DATA = Path(__file__).parent / "data" / "rf2"
SCT = rf2.SNOMED


def load(path=DATA, **kw):
    # The loader is an admin one-off: it runs as the database owner (P1-T19a)
    with schema_context("public"), as_owner():
        return rf2.load_release(path, operator="test-runner", **kw)[0]


def vs(compose):
    return ValueSet(
        slug="t", name="t", status="active", compose={"exclude": [], **compose}
    )


def isa(code):
    return {
        "include": [
            {
                "system": SCT,
                "filter": [{"property": "concept", "op": "is-a", "value": code}],
            }
        ]
    }


def zipped(tmp, members):
    path = Path(tmp) / "release.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def release_members():
    return {
        f"SnomedCT_Test/{p.relative_to(DATA)}": p.read_bytes()
        for p in DATA.rglob("*.txt")
    }


EXT = b"900000107"


def split_members():
    """The fixture as an edition (without 900000107) and an extension (only 900000107's rows)."""
    members = release_members()
    ext_desc = set()
    for name, data in members.items():
        if "Description" in name:
            ext_desc |= {
                line.split(b"\t")[0]
                for line in data.splitlines()[1:]
                if line.split(b"\t")[4] == EXT
            }
    edition, extension = {}, {}
    for name, data in members.items():
        head, *lines = data.splitlines(keepends=True)
        if "Concept" in name:
            mine = [x for x in lines if x.split(b"\t")[0] == EXT]
        elif "Description" in name or "Relationship" in name:
            mine = [x for x in lines if x.split(b"\t")[4] == EXT]
        else:
            mine = [x for x in lines if x.split(b"\t")[5] in ext_desc]
        edition[name] = head + b"".join(x for x in lines if x not in mine)
        extension[name.replace("SnomedCT_Test", "SnomedCT_TestExtension")] = (
            head + b"".join(mine)
        )
    return edition, extension


class Rf2Load(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.version = load()

    def concept(self, code):
        return Concept.objects.get(system=self.version, code=code)

    def test_concepts_displays_and_flags(self):
        self.assertEqual(
            (self.version.status, self.version.concepts, self.version.version),
            ("active", 7, "20261101"),
        )
        self.assertEqual(
            self.concept("900000102").display, "Zeta finding"
        )  # the GB preferred synonym
        self.assertEqual(
            self.concept("900000106").display, "Fsn only thing (finding)"
        )  # no synonym: the FSN
        self.assertFalse(self.concept("900000105").active)
        self.assertEqual(self.version.loaded_by, "test-runner")

    def test_hierarchy(self):
        ids = {c.code: c.id for c in Concept.objects.filter(system=self.version)}
        combined = self.concept("900000104")
        self.assertEqual(
            sorted(combined.parents), sorted([ids["900000102"], ids["900000103"]])
        )
        self.assertEqual(
            sorted(combined.ancestors),
            sorted([ids["900000101"], ids["900000102"], ids["900000103"]]),
        )
        # the inactive is-a row is ignored; so is a relationship of another type
        self.assertEqual(self.concept("900000106").parents, [ids["900000102"]])
        self.assertEqual(self.concept("900000107").parents, [ids["900000104"]])
        self.assertEqual(len(self.concept("900000107").ancestors), 4)

    def test_designations(self):
        terms = set(
            Designation.objects.filter(concept__system=self.version).values_list(
                "value", flat=True
            )
        )
        self.assertNotIn("Old zeta omega term", terms)  # an inactive description
        self.assertIn("पैरासिटामोल गोली", terms)
        fsn = Designation.objects.get(
            concept=self.concept("900000102"), use_code=str(rf2.FSN)
        )
        self.assertFalse(
            fsn.preferred
        )  # a fully specified name is never the preferred term
        self.assertFalse(
            Designation.objects.get(value="Zeta sign").preferred
        )  # acceptable only
        self.assertFalse(
            Designation.objects.filter(search_vector__isnull=True).exists()
        )

    def test_snomed_value_sets_answer_once_loaded(self):
        self.assertEqual(
            sorted(r.code for r in vs(isa("900000102")).search(count=50)),
            ["900000102", "900000104", "900000106", "900000107"],
        )
        ecl = {
            "include": [
                {
                    "system": SCT,
                    "filter": [
                        {
                            "property": "constraint",
                            "op": "=",
                            "value": "< 900000103 |Omega|",
                        }
                    ],
                }
            ]
        }
        self.assertEqual(
            sorted(r.code for r in vs(ecl).search(count=50)), ["900000104", "900000107"]
        )
        # CARE's medication picker adds " clinical drug": the FSN's tag matches
        self.assertEqual(
            [r.code for r in vs(isa("900000101")).search(search="para clinical drug")],
            ["900000107"],
        )
        self.assertEqual(
            [r.code for r in vs(isa("900000101")).search(search="पैरा")], ["900000107"]
        )
        self.assertTrue(
            vs(isa("900000102")).lookup(
                Coding(
                    system=SCT, code="900000107", display="Paracetamol 500 mg tablet"
                )
            )
        )
        self.assertFalse(
            vs(isa("900000103")).lookup(Coding(system=SCT, code="900000106"))
        )
        self.assertFalse(
            vs(isa("900000101")).lookup(Coding(system=SCT, code="900000105"))
        )  # inactive

    def test_a_second_release_retires_the_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "rf2"
            shutil.copytree(DATA, copy)
            for p in copy.rglob("*.txt"):
                p.write_text(
                    p.read_text().replace("Zeta finding", "Zeta finding, revised")
                )
            v2 = load(copy, version="20261201")
        self.version.refresh_from_db()
        self.assertEqual((self.version.status, v2.status), ("retired", "active"))
        self.assertEqual(
            engine.expand(isa("900000102"), "zeta revised")[0]["display"],
            "Zeta finding, revised",
        )


class Rf2Refusals(TestCase):
    def setUp(self):
        owner_for_test(self)  # ihr_terminology_load: an admin one-off, the owner

    def test_zip_read_without_extracting_and_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = zipped(tmp, release_members())
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(loader.LoadError):
                load(path, sha256="0" * 64)
            v = load(path, sha256=sha)
            self.assertEqual((v.sha256, v.concepts), (sha, 7))
            self.assertEqual(
                Concept.objects.get(system=v, code="900000102").properties["module"],
                "900000000000207008",
            )
            self.assertEqual(load(path).pk, v.pk)  # the same zip again: nothing new

    def test_unsafe_or_broken_releases(self):
        members = release_members()
        cases = {
            "a path outside": {**members, "../evil.txt": b"x"},
            "an absolute path": {**members, "/etc/evil.txt": b"x"},
            "no relationships": {
                k: v for k, v in members.items() if "Relationship" not in k
            },
            "wrong columns": {
                k: (b"id\tactive\n1\t1\n" if "Concept" in k else v)
                for k, v in members.items()
            },
            "a cycle": {
                k: (
                    v
                    + b"990001\t20261101\t1\t900000000000207008\t900000101\t900000104\t0\t116680003\t900000000000011006\t900000000000451002\n"
                    if "Relationship" in k
                    else v
                )
                for k, v in members.items()
            },
            "a bad id": {
                k: (v.replace(b"900000105\t", b"9x0000105\t") if "Concept" in k else v)
                for k, v in members.items()
            },
        }
        for name, case in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(loader.LoadError):
                    load(zipped(tmp, case))
                self.assertFalse(
                    CodeSystemVersion.objects.filter(url=SCT).exists()
                )  # nothing half-loaded

    def test_extension_loaded_with_its_edition_and_not_alone(self):
        edition, extension = split_members()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a").mkdir()
            (Path(tmp) / "b").mkdir()
            e, x = zipped(Path(tmp) / "a", edition), zipped(Path(tmp) / "b", extension)
            with self.assertRaises(loader.LoadError) as alone:
                load(x)
            self.assertIn(
                "load an extension together with the edition", str(alone.exception)
            )
            with schema_context("public"):
                v, summary = rf2.load_release([e, x], operator="test-runner")
        self.assertEqual(
            (v.concepts, summary["is_a"]), (7, 7)
        )  # the eighth is-a row is inactive
        self.assertEqual(
            Concept.objects.get(system=v, code="900000107").display,
            "Paracetamol 500 mg tablet",
        )

    def test_latest_row_wins_and_conflicts_are_refused(self):
        members = release_members()
        name = next(n for n in members if "Description" in n)
        later = (
            members[name]
            + b"990009\t20261201\t1\t900000000000207008\t900000103\ten\t900000000000013009\tOmega later\t900000000000448009\n"
        )
        lines = members[name].splitlines(keepends=True)
        restated = next(
            x for x in lines if b"Omega disorder\t" in x and b"900000000000013009" in x
        )
        newer = restated.replace(b"20261101", b"20261201").replace(
            b"Omega disorder", b"Omega disorder, newer"
        )
        clash = restated.replace(b"Omega disorder", b"Omega clash")
        with tempfile.TemporaryDirectory() as tmp:
            v = load(zipped(tmp, {**members, name: later + newer}))
            # the same description id, restated later: its newer text is the display now
            self.assertEqual(
                Concept.objects.get(system=v, code="900000103").display,
                "Omega disorder, newer",
            )
            self.assertTrue(
                Designation.objects.filter(
                    concept__system=v, value="Omega disorder, newer"
                ).exists()
            )
            self.assertFalse(
                Designation.objects.filter(
                    concept__system=v, value="Omega disorder"
                ).exists()
            )
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(loader.LoadError):
            load(zipped(tmp, {**members, name: members[name] + clash}))

    def test_server_rules(self):
        """Outside bench and test databases: zips only, with sha256 and a licence; never the synthetic release."""
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(rf2, "throwaway_database", return_value=False),
        ):
            path = zipped(tmp, release_members())
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            for kwargs in ({}, {"sha256": [sha]}, {"licence": "MLDS-IN-1"}):
                with self.subTest(kwargs=kwargs), self.assertRaises(loader.LoadError):
                    load(path, **kwargs)
            with self.assertRaises(loader.LoadError):
                load(DATA, sha256=["0" * 64], licence="x")  # a folder
            synthetic = {
                k: v.replace(b"900000000000207008", str(rf2.SYNTHETIC_MODULE).encode())
                for k, v in release_members().items()
            }
            (Path(tmp) / "s").mkdir()
            spath = zipped(Path(tmp) / "s", synthetic)
            with self.assertRaises(loader.LoadError) as refused:
                load(
                    spath,
                    sha256=[hashlib.sha256(spath.read_bytes()).hexdigest()],
                    licence="x",
                )
            self.assertIn("synthetic", str(refused.exception))
            v = load(path, sha256=[sha], licence="MLDS-IN-1")
            self.assertEqual(v.licence, "MLDS-IN-1")

    def test_language_refsets_per_language(self):
        hindi_refset = "900000999"
        members = release_members()
        name = next(n for n in members if "Language" in n)
        desc = next(n for n in members if "Description" in n)
        hindi_id = next(
            x.split(b"\t")[0]
            for x in members[desc].splitlines()
            if "पैरासिटामोल".encode() in x
        )
        member = f"00000000-0000-0000-0000-00000000ffff\t20261101\t1\t900000000000207008\t{hindi_refset}\t{hindi_id.decode()}\t900000000000548007\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = zipped(tmp, {**members, name: members[name] + member.encode()})
            with schema_context("public"):
                v, _ = rf2.load_release(
                    path,
                    operator="t",
                    language_refsets=["en:900000000000508004", f"hi:{hindi_refset}"],
                )
        hindi = Designation.objects.get(concept__system=v, language="hi")
        self.assertTrue(hindi.preferred)
        self.assertEqual(
            engine.expand(isa("900000101"), "para", display_language="hi")[0][
                "display"
            ],
            "पैरासिटामोल गोली",
        )
        self.assertFalse(
            Designation.objects.filter(
                concept__system=v, use_code=str(rf2.FSN), preferred=True
            ).exists()
        )

    def test_english_display_without_an_english_refset(self):
        """Only a Hindi refset given: GB English still gives the display and the preferred
        English term (review L1); a different language list is a new load, not "unchanged" (L2)."""
        with schema_context("public"):
            first, _ = rf2.load_release(DATA, operator="t")
            v, summary = rf2.load_release(
                DATA, operator="t", language_refsets=["hi:900000999"]
            )
        self.assertNotEqual(v.pk, first.pk)
        self.assertNotIn("unchanged", summary)
        c = Concept.objects.get(system=v, code="900000102")
        self.assertEqual(c.display, "Zeta finding")
        self.assertTrue(
            Designation.objects.filter(
                concept=c, language="en", preferred=True
            ).exists()
        )

    def test_unchanged_only_when_that_version_is_active(self):
        """The same release again is "unchanged" while it is active; after a newer release
        retired it, loading it again is a real load (a rollback), never a silent no-op (L2)."""
        with tempfile.TemporaryDirectory() as tmp, schema_context("public"):
            path = zipped(tmp, release_members())
            v1, _ = rf2.load_release(path, operator="t")
            again, summary = rf2.load_release(path, operator="t")
            self.assertEqual((again.pk, summary.get("unchanged")), (v1.pk, True))
            newer = {
                k: v.replace(b"Omega disorder", b"Omega disorder, new")
                for k, v in release_members().items()
            }
            (Path(tmp) / "n").mkdir()
            rf2.load_release(
                zipped(Path(tmp) / "n", newer), operator="t", version="20261201"
            )
            back, summary = rf2.load_release(path, operator="t")
        self.assertNotIn("unchanged", summary)
        self.assertEqual(back.status, "active")
        self.assertEqual(
            engine.expand(isa("900000103"), "omega")[0]["display"], "Omega disorder"
        )

    def test_session_settings_are_restored(self):
        """The load raises work_mem and lifts the statement timeout for itself only (L7)."""
        from django.db import connection, transaction

        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '1234ms'")
            cur.execute("SELECT current_setting('work_mem')")
            work_mem = cur.fetchone()[0]
            load()
            cur.execute(
                "SELECT current_setting('statement_timeout'), current_setting('work_mem')"
            )
            self.assertEqual(cur.fetchone(), ("1234ms", work_mem))

    def test_needs_an_operator_and_public(self):
        with self.assertRaises(loader.LoadError):
            rf2.load_release(DATA, operator="")
        with self.assertRaises(loader.LoadError):  # from a hospital's context
            rf2.load_release(DATA, operator="x")
