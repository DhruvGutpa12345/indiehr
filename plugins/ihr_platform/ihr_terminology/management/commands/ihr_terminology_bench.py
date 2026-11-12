# ruff: noqa: S311, FBT003, PLR2004  (a benchmark tool: seeded pseudo-random synthetic data, tuning constants)
"""SNOMED-scale benchmark for the terminology engine (P1-T10a2; decides Q-F6, ADR-016).

    ihr_terminology_bench generate --out <dir> [--active 370000] [--inactive 150000] [--drugs 100000]
        Writes two synthetic RF2 Snapshot packages, <dir>/edition and <dir>/extension, sized like
        the content they stand in for: the International Edition (active and inactive concepts)
        and the India Drug Extension (drug concepts under the edition's drug branch, with long
        names, strengths and units). Made-up ids, words and module ids, never SNOMED content; the
        loader refuses the synthetic module outside bench databases. It prints the counts reached
        and whether they meet the targets (a verdict needs them).
    ihr_terminology_bench measure [--runs 200] [--clients 4]
        Times the engine's answers with the query shapes CARE's web app really sends, and prints
        JSON lines: p50/p95/max, the median number of matches, timeouts.

Both refuse to run unless the database is named bench_* (scripts/terminology-scale-bench.sh starts
a throwaway one); `generate` writes files only.
"""

import json
import math
import random
import resource
import statistics
import string
import threading
import time
from pathlib import Path

from django.conf import settings
from django.contrib.postgres.search import SearchQuery
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from ihr_terminology import engine
from ihr_terminology.models import CodeSystemVersion, Concept, Designation
from ihr_terminology.rf2 import SYNTHETIC_MODULE

SYSTEM = "http://snomed.info/sct"
ROOT_ID = 200000000
TOP_LEVEL = 19
DRUG_BRANCH = (
    5  # TAGS[5]: the branch CARE's medication picker searches (<< "Medicinal product")
)
IS_A = "116680003"
FSN, SYNONYM = "900000000000003001", "900000000000013009"
PREFERRED, ACCEPTABLE = "900000000000548007", "900000000000549004"
GB_REFSET = "900000000000508004"
EXT_MODULE = SYNTHETIC_MODULE + 1
TAGS = ["finding", "disorder", "procedure", "body structure", "substance", "clinical drug",
        "organism", "observable entity", "qualifier value", "specimen", "event", "situation",
        "physical object", "environment", "social concept", "staging scale", "record artifact",
        "regime/therapy", "morphologic abnormality"]  # fmt: skip
FORMS = ["oral tablet", "oral capsule", "oral suspension", "solution for injection", "cutaneous cream",
         "eye drops", "prolonged-release oral tablet", "powder for oral solution", "inhalation powder"]  # fmt: skip
UNITS = [
    ("milligram", "mg"),
    ("microgram", "microgram"),
    ("gram", "g"),
    ("milliliter", "mL"),
    ("unit", "unit"),
]
EDITION_DATE, EXTENSION_DATE = "20261101", "20260831"
# Sized like the International Edition plus the India Drug Extension; a verdict needs every count
# within TOLERANCE of its target (the P1-T10a2 design attack: "about 5 %")
TARGETS = {"is_a": 600000, "descriptions": 1900000}
TOLERANCE = 0.95


def _vocabulary(rng, n=20000):
    syllables = [a + b for a in "bcdfghjklmnprstvz" for b in "aeiou"] + [
        "an",
        "el",
        "or",
        "ix",
        "um",
        "os",
    ]
    words = set()
    while len(words) < n:
        words.add(
            "".join(
                rng.choice(syllables) for _ in range(rng.choice((2, 2, 3, 3, 3, 4)))
            )
        )
    return sorted(words)


class _Writer:
    """One synthetic package's four Snapshot files."""

    def __init__(self, root, date, module):
        self.date, self.module = date, module
        term, lang = (
            root / "Snapshot" / "Terminology",
            root / "Snapshot" / "Refset" / "Language",
        )
        term.mkdir(parents=True, exist_ok=True)
        lang.mkdir(parents=True, exist_ok=True)
        self.c = (term / f"sct2_Concept_Snapshot_INT_{date}.txt").open("w")
        self.r = (term / f"sct2_Relationship_Snapshot_INT_{date}.txt").open("w")
        self.d = (term / f"sct2_Description_Snapshot-en_INT_{date}.txt").open("w")
        self.l = (lang / f"der2_cRefset_LanguageSnapshot-en_INT_{date}.txt").open("w")
        self.c.write("id\teffectiveTime\tactive\tmoduleId\tdefinitionStatusId\n")
        self.r.write(
            "id\teffectiveTime\tactive\tmoduleId\tsourceId\tdestinationId\trelationshipGroup\ttypeId\tcharacteristicTypeId\tmodifierId\n"
        )
        self.d.write(
            "id\teffectiveTime\tactive\tmoduleId\tconceptId\tlanguageCode\ttypeId\tterm\tcaseSignificanceId\n"
        )
        self.l.write(
            "id\teffectiveTime\tactive\tmoduleId\trefsetId\treferencedComponentId\tacceptabilityId\n"
        )
        self.counts = {"concepts": 0, "is_a": 0, "descriptions": 0}

    def concept(self, cid, active):
        self.c.write(
            f"{cid}\t{self.date}\t{int(active)}\t{self.module}\t900000000000074008\n"
        )
        self.counts["concepts"] += 1

    def isa(self, rid, child, parent, active=True):
        self.r.write(
            f"{rid}\t{self.date}\t{int(active)}\t{self.module}\t{child}\t{parent}\t0\t{IS_A}\t900000000000011006\t900000000000451002\n"
        )
        self.counts["is_a"] += int(active)

    def term(self, did, cid, type_id, text, acceptability, member):
        self.d.write(
            f"{did}\t{self.date}\t1\t{self.module}\t{cid}\ten\t{type_id}\t{text}\t900000000000448009\n"
        )
        uid = f"{member:032x}"
        self.l.write(
            f"{uid[:8]}-{uid[8:12]}-{uid[12:16]}-{uid[16:20]}-{uid[20:]}\t{self.date}\t1\t{self.module}\t{GB_REFSET}\t{did}\t{acceptability}\n"
        )
        self.counts["descriptions"] += 1

    def close(self):
        for f in (self.c, self.r, self.d, self.l):
            f.close()


class Command(BaseCommand):
    help = "Synthetic SNOMED-scale release and timings for the terminology engine (bench_* databases only)"

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        g = sub.add_parser("generate")
        g.add_argument("--out", required=True)
        g.add_argument("--active", type=int, default=370000)
        g.add_argument("--inactive", type=int, default=150000)
        g.add_argument("--drugs", type=int, default=100000)
        g.add_argument("--seed", type=int, default=1)
        m = sub.add_parser("measure")
        m.add_argument("--runs", type=int, default=200)
        m.add_argument("--clients", type=int, default=4)
        m.add_argument("--seed", type=int, default=2)

    def handle(self, *args, **o):
        name = settings.DATABASES["default"]["NAME"]
        if not str(name).startswith("bench_"):
            msg = f"refusing: the database is {name!r}, not a bench_* database"
            raise CommandError(msg)
        if o["action"] == "generate":
            self.generate(
                Path(o["out"]), o["active"], o["inactive"], o["drugs"], o["seed"]
            )
        else:
            self.measure(o["runs"], o["clients"], o["seed"])

    # ---- generate -------------------------------------------------------------------------
    def generate(self, out, n_active, n_inactive, n_drugs, seed):  # noqa: PLR0915 (one release, step by step)
        rng = random.Random(seed)
        vocab = _vocabulary(rng)
        weights = [
            1 / (i + 1) ** 0.9 for i in range(len(vocab))
        ]  # Zipf-like word frequencies
        ed = _Writer(out / "edition", EDITION_DATE, SYNTHETIC_MODULE)
        ext = _Writer(out / "extension", EXTENSION_DATE, EXT_MODULE)
        ids = {"rel": 300000000, "desc": 400000000, "member": 0}

        def next_id(kind):
            ids[kind] += 1
            return ids[kind]

        def terms(writer, cid, term, tag, synonyms):
            writer.term(
                next_id("desc"),
                cid,
                FSN,
                f"{term} ({tag})",
                PREFERRED,
                next_id("member"),
            )
            writer.term(
                next_id("desc"), cid, SYNONYM, term, PREFERRED, next_id("member")
            )
            for s in synonyms:
                writer.term(
                    next_id("desc"), cid, SYNONYM, s, ACCEPTABLE, next_id("member")
                )

        def general_term():
            words = rng.choices(vocab, weights, k=rng.choice((2, 2, 3, 3, 4, 4, 5, 6)))
            return words, " ".join(words).capitalize()

        # The edition's active hierarchy: a root, 19 branches (one a third of all), 1-3 parents
        branch_weights = [0.33] + [0.67 / (TOP_LEVEL - 1)] * (TOP_LEVEL - 1)
        depth, members, branch_of = {ROOT_ID: 0}, [[] for _ in range(TOP_LEVEL)], {}
        ed.concept(ROOT_ID, True)
        terms(ed, ROOT_ID, "Synthetic root", "root", [])
        for b in range(TOP_LEVEL):
            cid = ROOT_ID + 1 + b
            ed.concept(cid, True)
            ed.isa(next_id("rel"), cid, ROOT_ID)
            terms(ed, cid, f"{TAGS[b].capitalize()} branch", TAGS[b], [])
            depth[cid], branch_of[cid] = 1, b
            members[b].append(cid)
        cid = ROOT_ID + TOP_LEVEL
        for _ in range(n_active - TOP_LEVEL - 1):
            cid += 1
            b = rng.choices(range(TOP_LEVEL), branch_weights)[0]
            pool = members[b]
            parents = []
            for _ in range(rng.choices((1, 2, 3), (70, 22, 8))[0]):
                p = pool[rng.randrange(len(pool))]
                if (
                    depth[p] < 17 and p not in parents
                ):  # SNOMED's deepest paths are about 18 long
                    parents.append(p)
            if not parents:
                parents = [pool[0]]
            ed.concept(cid, True)
            for p in parents:
                ed.isa(next_id("rel"), cid, p)
            words, term = general_term()
            if b == DRUG_BRANCH:
                term = f"{term} {rng.choice((5, 10, 25, 50, 100, 250, 500))} mg {rng.choice(FORMS)}"
            synonyms = []
            for _ in range(rng.choice((0, 1, 1, 2, 3))):
                alt = words[:]
                rng.shuffle(alt)
                alt.append(rng.choices(vocab, weights)[0])
                synonyms.append(" ".join(alt))
            terms(ed, cid, term, TAGS[b], synonyms)
            depth[cid], branch_of[cid] = 1 + max(depth[p] for p in parents), b
            pool.append(cid)
        # Inactive concepts: no active is-a rows, their descriptions still active (as in SNOMED)
        for _ in range(n_inactive):
            cid += 1
            ed.concept(cid, False)
            ed.isa(
                next_id("rel"),
                cid,
                ROOT_ID + 1 + rng.randrange(TOP_LEVEL),
                active=False,
            )
            words, term = general_term()
            terms(ed, cid, term, rng.choice(TAGS), [])
        # The drug extension: products under the edition's drug branch, long names with numbers
        drug_pool = members[DRUG_BRANCH]
        substances = rng.sample(vocab, 3000)
        for _ in range(n_drugs):
            cid += 1
            parents = list(
                {
                    drug_pool[rng.randrange(len(drug_pool))]
                    for _ in range(rng.choices((1, 2), (75, 25))[0])
                }
            )
            ext.concept(cid, True)
            for p in parents:
                ext.isa(next_id("rel"), cid, p)
            ingredient = rng.choice(substances).capitalize()
            strength = rng.choice(
                (
                    5,
                    10,
                    20,
                    25,
                    40,
                    50,
                    75,
                    100,
                    125,
                    150,
                    200,
                    250,
                    400,
                    500,
                    650,
                    1000,
                )
            )
            unit, short = rng.choice(UNITS)
            form = rng.choice(FORMS)
            fsn = f"Product containing precisely {ingredient.lower()} {strength} {unit}/1 each conventional release {form}"
            terms(
                ext,
                cid,
                fsn,
                "clinical drug",
                [f"{ingredient} {strength} {short} {form}"],
            )
        ed.close()
        ext.close()
        reached = {k: ed.counts[k] + ext.counts[k] for k in ed.counts}
        target_concepts = n_active + n_inactive + n_drugs
        calibrated = (reached["concepts"] >= TOLERANCE * target_concepts
                      and reached["is_a"] >= TOLERANCE * TARGETS["is_a"]
                      and reached["descriptions"] >= TOLERANCE * TARGETS["descriptions"])  # fmt: skip
        self.stdout.write(json.dumps({
            "generated": reached, "edition": ed.counts, "extension": ext.counts,
            "largest_branch_size": len(members[0]), "drug_branch_size": len(drug_pool) + n_drugs,
            "max_depth": max(depth.values()), "targets": TARGETS, "tolerance": TOLERANCE, "calibrated": calibrated,
        }))  # fmt: skip

    # ---- measure --------------------------------------------------------------------------
    def measure(self, runs, clients, seed):  # noqa: PLR0912, PLR0915 (one benchmark, shape by shape)
        v = CodeSystemVersion.objects.filter(url=SYSTEM, status="active").first()
        if not v:
            msg = "no active synthetic release: load it first"
            raise CommandError(msg)
        rng = random.Random(seed)
        big_root = Concept.objects.get(system=v, code=str(ROOT_ID + 1))
        drug_root = Concept.objects.get(system=v, code=str(ROOT_ID + 1 + DRUG_BRANCH))
        # Real searches: 2-3 words of one preferred term under the subtree, each cut to 3-6 letters
        sample_terms = list(
            Designation.objects.filter(concept__system=v, preferred=True, concept__ancestors__contains=[big_root.id])
            .values_list("search_text", flat=True).order_by("?")[:3000]
        )  # fmt: skip
        sample_words = [t.split() for t in sample_terms if len(t.split()) >= 2]
        codes = list(
            Concept.objects.filter(system=v, active=True)
            .values_list("code", flat=True)
            .order_by("?")[:5000]
        )
        big = {
            "include": [
                {
                    "system": SYSTEM,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": big_root.code}
                    ],
                }
            ]
        }
        drugs = {"include": [{"system": SYSTEM, "filter": [{"property": "constraint", "op": "=",
                                                            "value": f"<< {drug_root.code} |drugs|"}]}]}  # fmt: skip

        def words_query():
            ws = (
                rng.sample(sample_words[rng.randrange(len(sample_words))], 2)
                if rng.random() < 0.7
                else sample_words[rng.randrange(len(sample_words))][:3]
            )
            return " ".join(w[: rng.randint(3, 6)] for w in ws)

        shapes = {
            "words_2_3": lambda: (big, words_query()),
            "one_word_3": lambda: (big, rng.choice(sample_words)[0][:3]),
            "one_letter": lambda: (big, rng.choice(string.ascii_lowercase)),
            "two_letters": lambda: (big, rng.choice(sample_words)[0][:2]),
            "picker_opened": lambda: (big, ""),
            "medication_opened": lambda: (
                drugs,
                " clinical drug",
            ),  # CARE adds " clinical drug" even when empty
            "medication_1_letter": lambda: (
                drugs,
                rng.choice(string.ascii_lowercase) + " clinical drug",
            ),
            "medication_word": lambda: (
                drugs,
                rng.choice(sample_words)[0][:4] + " clinical drug",
            ),
        }
        sizes = {"big_subtree": Concept.objects.filter(system=v, ancestors__contains=[big_root.id]).count(),
                 "drug_subtree": Concept.objects.filter(system=v, ancestors__contains=[drug_root.id]).count(),
                 "concepts": v.concepts, "designations": Designation.objects.filter(concept__system=v).count()}  # fmt: skip
        self.stdout.write(json.dumps({"sizes": sizes}))
        rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        for name, make in shapes.items():
            times, timeouts, matches = [], 0, []
            for i in range(runs + 5):
                compose, text = make()
                t = time.perf_counter()
                try:
                    engine.expand(compose, text, 10)
                    took = (time.perf_counter() - t) * 1000
                except engine.TooSlow:
                    took = None
                if i < 5:  # five warm-up calls
                    continue
                if (
                    took is None
                ):  # a timeout counts in the latency figures at the limit it hit
                    timeouts += 1
                    took = float(engine.STATEMENT_TIMEOUT_MS)
                times.append(took)
                if (
                    i % 20 == 0 and text.strip()
                ):  # how wide the shape is, in its own subtree (untimed)
                    words = engine._lexemes(engine.normalise(text))  # noqa: SLF001
                    tsq = " & ".join(f"{w}:*" for w in words)
                    query = SearchQuery(tsq, config="simple", search_type="raw")
                    root = drug_root if compose is drugs else big_root
                    matches.append(Designation.objects.filter(concept__system=v, concept__ancestors__contains=[root.id],
                                                              search_vector=query).values("concept_id").distinct().count()
                                   if words else 0)  # fmt: skip
            self.stdout.write(
                json.dumps(self._stats(name, times, 1, timeouts, matches))
            )
        for name, fn in {"validate_code": lambda: engine.validate(big, {"system": SYSTEM, "code": rng.choice(codes)}),
                         "lookup": lambda: engine.lookup(SYSTEM, rng.choice(codes))}.items():  # fmt: skip
            times = []
            for i in range(runs + 5):
                t = time.perf_counter()
                fn()
                if i >= 5:
                    times.append((time.perf_counter() - t) * 1000)
            self.stdout.write(json.dumps(self._stats(name, times, 1, 0, [])))
        # Several clients at once, the shapes a busy OPD sends
        mixed = ["words_2_3", "two_letters", "medication_opened", "medication_1_letter"]
        times, timeouts, lock = [], [0], threading.Lock()
        errors = []

        def client(k):
            local = random.Random(seed + k)
            try:
                for _ in range(runs // clients):
                    compose, text = shapes[local.choice(mixed)]()
                    t = time.perf_counter()
                    try:
                        engine.expand(compose, text, 10)
                    except engine.TooSlow:
                        with lock:
                            timeouts[0] += 1
                            times.append(float(engine.STATEMENT_TIMEOUT_MS))
                        continue
                    with lock:
                        times.append((time.perf_counter() - t) * 1000)
            except (
                Exception
            ) as e:  # any failure fails the run below, never a shorter sample
                with lock:
                    errors.append(f"client {k}: {type(e).__name__}: {e}")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=client, args=(k,)) for k in range(clients)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        if errors:
            self.stdout.write(
                json.dumps({"shape": f"mixed_x{clients}", "errors": errors})
            )
            msg = f"{len(errors)} client(s) failed: no figures from this run"
            raise CommandError(msg)
        self.stdout.write(
            json.dumps(
                self._stats(f"mixed_x{clients}", times, clients, timeouts[0], [])
            )
        )
        rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        with connections["default"].cursor() as cur:
            cur.execute(
                "SELECT pg_total_relation_size('ihr_terminology_concept') + pg_total_relation_size('ihr_terminology_designation')"
            )
            disk = cur.fetchone()[0]
        self.stdout.write(json.dumps({"disk_bytes": disk, "backend_peak_rss_mb": rss_after // 1024,
                                      "backend_rss_growth_mb": (rss_after - rss_before) // 1024}))  # fmt: skip

    @staticmethod
    def _stats(name, times, clients, timeouts, matches):
        times = sorted(times) or [0.0]
        return {"shape": name, "clients": clients, "n": len(times), "timeouts": timeouts,
                "p50_ms": round(statistics.median(times), 1), "p95_ms": round(times[max(0, math.ceil(0.95 * len(times)) - 1)], 1),
                "max_ms": round(times[-1], 1), "median_matching_concepts": int(statistics.median(matches)) if matches else None}  # fmt: skip
