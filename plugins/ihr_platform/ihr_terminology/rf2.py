"""Loads SNOMED CT RF2 Snapshot releases as one version (P1-T10a2, ADR-016).

India's content ships as several packages (P1-T10b): the International Edition, then NRCeS's
extensions (the India Drug Extension and others), each depending on the one before. They are
loaded together, as one version:

    manage.py ihr_terminology_load --rf2 <international.zip> --rf2 <india-drug-extension.zip> ...

From each package (a zip read member by member, never extracted) the Snapshot files are read:

    sct2_Concept_Snapshot_*.txt               codes, active flag, module
    sct2_Description_Snapshot-*_*.txt         fully specified names and synonyms
    sct2_Relationship_Snapshot_*.txt          is-a rows (typeId 116680003)
    der2_cRefset_LanguageSnapshot-*_*.txt     preferred / acceptable terms per language refset

Rules:
- Rows of all packages are copied into staging tables, with their effectiveTime. Per component
  id the latest row wins; two different rows with the same id and time are refused.
- Active descriptions and is-a rows must point at concepts of the load: an extension loaded
  without the edition it depends on is refused, never loaded with silently missing links.
- Concept ids are assigned first, parents and ancestors (level by level: a frontier joined to
  direct parents; a cycle shows as a concept being its own ancestor) are computed in staging
  tables, and each concept row is written once, with its final arrays.
- Display: the preferred synonym in the first English language refset that has one (GB English
  by default, then any others given), else the fully specified name. A designation is preferred
  when it is a synonym preferred by a language refset mapped to its own language.
- The whole load is one transaction: a new version is written, counted and switched to active,
  or nothing changes. It holds one transaction id for its duration, so the audit trail's sealing
  and vacuum wait (ADR-016: load in an off-hours window).
- Outside bench_* and test_* databases: a release must be a zip with its sha256 and a licence
  reference, and the synthetic benchmark release (its module id) is refused.
"""

import hashlib
import io
import logging
import re
import socket
import zipfile
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction

from ihr_terminology.loader import LoadError, _close_for_writing, _open_for_writing
from ihr_terminology.models import CodeSystemVersion
from ihr_terminology.text import normalise

log = logging.getLogger("ihr.terminology")

SNOMED = "http://snomed.info/sct"
IS_A = 116680003
FSN = 900000000000003001
SYNONYM = 900000000000013009
PREFERRED = 900000000000548007
GB_ENGLISH = 900000000000508004
SYNTHETIC_MODULE = (
    123000000000000001  # ihr_terminology_bench's made-up releases carry this module
)
MAX_MEMBERS = 10000
MAX_MEMBER_BYTES = 2 * 2**30
MAX_LINE = 64 * 1024
S_IFLNK = 0o120000  # a symbolic link's file type in a zip member's Unix mode
MAX_ROUNDS = (
    100  # a backstop only: SNOMED is about 20 deep, and cycles are found another way
)

FILES = {
    "concept": (
        re.compile(r"(^|/)sct2_Concept_Snapshot_[^/]+\.txt$"),
        ["id", "effectiveTime", "active", "moduleId", "definitionStatusId"],
    ),
    "description": (
        re.compile(r"(^|/)sct2_Description_Snapshot-[^/]+\.txt$"),
        ["id", "effectiveTime", "active", "moduleId", "conceptId", "languageCode", "typeId", "term",
         "caseSignificanceId"],
    ),
    "relationship": (
        re.compile(r"(^|/)sct2_Relationship_Snapshot_[^/]+\.txt$"),
        ["id", "effectiveTime", "active", "moduleId", "sourceId", "destinationId", "relationshipGroup",
         "typeId", "characteristicTypeId", "modifierId"],
    ),
    "language": (
        re.compile(r"(^|/)der2_cRefset_LanguageSnapshot-[^/]+\.txt$"),
        ["id", "effectiveTime", "active", "moduleId", "refsetId", "referencedComponentId", "acceptabilityId"],
    ),
}  # fmt: skip
_SCTID = re.compile(r"[1-9][0-9]{5,17}")
_DATE = re.compile(r"[0-9]{8}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TEMP = ("rf2_c_raw", "rf2_d_raw", "rf2_r_raw", "rf2_l_raw", "rf2_concept", "rf2_desc", "rf2_isa", "rf2_lang",
        "rf2_id", "rf2_par", "rf2_anc", "rf2_front", "rf2_next", "rf2_disp")  # fmt: skip


def throwaway_database():
    """Only the test settings and the benchmark say so (IHR_TERMINOLOGY_THROWAWAY_DB), and only
    for a database named test_* or bench_*: a name alone never relaxes the rules, and the
    deployment settings refuse such names (settings/secure.py)."""
    name = str(settings.DATABASES["default"]["NAME"])
    return bool(
        getattr(settings, "IHR_TERMINOLOGY_THROWAWAY_DB", False)
    ) and name.startswith(("bench_", "test_"))


class Release:
    """One package's Snapshot files, from a zip or (bench and test databases only) a folder."""

    def __init__(self, path, sha256=None):  # noqa: PLR0912, PLR0915 (one check per way a release can be unsafe)
        self.path = Path(path)
        self.zip = None
        if self.path.is_file():
            digest = hashlib.sha256()
            with self.path.open("rb") as f:
                for block in iter(lambda: f.read(2**20), b""):
                    digest.update(block)
            self.sha256 = digest.hexdigest()
            if sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", str(sha256)):
                msg = f"{self.path.name}: --sha256 must be 64 hex characters"
                raise LoadError(msg)
            if sha256 and self.sha256 != str(sha256).lower():
                msg = f"{self.path.name}: sha256 {self.sha256} is not the expected {sha256}"
                raise LoadError(msg)
            try:
                self.zip = zipfile.ZipFile(self.path)
            except zipfile.BadZipFile as e:
                msg = f"{self.path.name}: not a zip ({e})"
                raise LoadError(msg) from e
            infos = self.zip.infolist()
            for info in infos:
                if info.flag_bits & 0x1 or info.compress_type not in (
                    zipfile.ZIP_STORED,
                    zipfile.ZIP_DEFLATED,
                ):
                    msg = f"{self.path.name}: {info.filename!r} is encrypted or compressed in a way we do not read"
                    raise LoadError(msg)
            if len(infos) > MAX_MEMBERS:
                msg = f"{self.path.name}: too many members"
                raise LoadError(msg)
            for info in infos:
                name = info.filename
                if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
                    msg = f"{self.path.name}: refusing a member path {name!r}"
                    raise LoadError(msg)
                if (info.external_attr >> 16) & 0o170000 == S_IFLNK:
                    msg = f"{self.path.name}: refusing a symbolic link {name!r}"
                    raise LoadError(msg)
                if info.file_size > MAX_MEMBER_BYTES:
                    msg = f"{self.path.name}: refusing a member over {MAX_MEMBER_BYTES // 2**30} GB: {name!r}"
                    raise LoadError(msg)
            self.names = [i.filename for i in infos if not i.is_dir()]
        elif self.path.is_dir():
            if not throwaway_database():
                msg = "a release folder is accepted on bench and test databases only: give the release zip"
                raise LoadError(msg)
            self.sha256 = "folder"
            self.names = [
                str(p.relative_to(self.path))
                for p in self.path.rglob("*.txt")
                if not p.is_symlink()
            ]
        else:
            msg = f"{self.path}: no such release"
            raise LoadError(msg)
        self.found = {}
        for kind, (pattern, _) in FILES.items():
            matches = sorted(n for n in self.names if pattern.search(n))
            if kind in ("concept", "relationship") and len(matches) != 1:
                msg = f"{self.path.name}: expected one {kind} snapshot file, found {len(matches)}"
                raise LoadError(msg)
            self.found[kind] = matches
        if not self.found["description"]:
            msg = f"{self.path.name}: no description snapshot file"
            raise LoadError(msg)
        self.date = re.sub(r".*_(\d{8})\.txt$", r"\1", self.found["concept"][0])

    def rows(self, kind):
        """Yield each data row of each file of this kind as a list of strings."""
        try:
            yield from self._rows(kind)
        except (UnicodeDecodeError, zipfile.BadZipFile, EOFError, OSError) as e:
            msg = f"{self.path.name}: unreadable {kind} file ({e})"
            raise LoadError(msg) from e

    def _rows(self, kind):
        _, header = FILES[kind]
        for name in self.found[kind]:
            raw = self.zip.open(name) if self.zip else (self.path / name).open("rb")
            with raw, io.TextIOWrapper(raw, encoding="utf-8", newline="") as text:
                first = text.readline(MAX_LINE).rstrip("\r\n").split("\t")
                if first != header:
                    msg = f"{name}: unexpected columns {first[:4]}..."
                    raise LoadError(msg)
                n = 1
                while line := text.readline(MAX_LINE + 1):
                    n += 1
                    if len(line) > MAX_LINE:
                        msg = f"{name}:{n}: line too long"
                        raise LoadError(msg)
                    cols = line.rstrip("\r\n").split("\t")
                    if len(cols) != len(header):
                        msg = f"{name}:{n}: {len(cols)} columns, expected {len(header)}"
                        raise LoadError(msg)
                    yield cols


def _id(value, where):
    if not _SCTID.fullmatch(value):
        msg = f"{where}: not a SNOMED id: {value[:20]!r}"
        raise LoadError(msg)
    return int(value)


def _date(value, where):
    if not _DATE.fullmatch(value):
        msg = f"{where}: not an effectiveTime: {value[:12]!r}"
        raise LoadError(msg)
    return int(value)


def _flag(value, where):
    if value not in ("0", "1"):
        msg = f"{where}: active must be 0 or 1"
        raise LoadError(msg)
    return value == "1"


def _copy(cur, table, columns, rows):
    with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
        for r in rows:
            copy.write_row(r)


def _one(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()[0]


def parse_language_refsets(values):
    """['en:900000000000508004', 'hi:<id>'] -> [('en', id), ('hi', id)] in priority order."""
    out = []
    for v in values or [f"en:{GB_ENGLISH}"]:
        lang, _, refset = str(v).partition(":")
        if not re.fullmatch(r"[a-z]{2,3}", lang) or not _SCTID.fullmatch(refset):
            msg = f"--language-refset must be <language>:<refset id>, e.g. en:{GB_ENGLISH}"
            raise LoadError(msg)
        out.append((lang, int(refset)))
    return out


def _stage(cur, releases, refsets):
    cur.execute("""
        CREATE TEMP TABLE rf2_c_raw (id bigint, et int, active boolean, module bigint) ON COMMIT DROP;
        CREATE TEMP TABLE rf2_d_raw (id bigint, et int, active boolean, module bigint, concept bigint, lang text,
                                     type bigint, term text, search text) ON COMMIT DROP;
        CREATE TEMP TABLE rf2_r_raw (id bigint, et int, active boolean, child bigint, parent bigint) ON COMMIT DROP;
        CREATE TEMP TABLE rf2_l_raw (id uuid, et int, active boolean, refset bigint, description bigint,
                                     acceptability bigint) ON COMMIT DROP;
    """)
    for rel in releases:
        w = rel.path.name
        _copy(
            cur,
            "rf2_c_raw",
            ["id", "et", "active", "module"],
            (
                (_id(r[0], w), _date(r[1], w), _flag(r[2], w), _id(r[3], w))
                for r in rel.rows("concept")
            ),
        )
        _copy(
            cur,
            "rf2_d_raw",
            [
                "id",
                "et",
                "active",
                "module",
                "concept",
                "lang",
                "type",
                "term",
                "search",
            ],
            (
                (
                    _id(r[0], w),
                    _date(r[1], w),
                    _flag(r[2], w),
                    _id(r[3], w),
                    _id(r[4], w),
                    r[5][:16].lower(),
                    _id(r[6], w),
                    r[7],
                    normalise(r[7]),
                )
                for r in rel.rows("description")
            ),
        )
        _copy(
            cur,
            "rf2_r_raw",
            ["id", "et", "active", "child", "parent"],
            (
                (
                    _id(r[0], w),
                    _date(r[1], w),
                    _flag(r[2], w),
                    _id(r[4], w),
                    _id(r[5], w),
                )
                for r in rel.rows("relationship")
                if r[7] == str(IS_A)
            ),
        )

        def members(rel=rel, w=w):
            for r in rel.rows("language"):
                if not _UUID.fullmatch(r[0]):
                    msg = (
                        f"{w}: a language refset member id is not a UUID: {r[0][:40]!r}"
                    )
                    raise LoadError(msg)
                if _id(r[4], w) in refsets:
                    yield (
                        r[0],
                        _date(r[1], w),
                        _flag(r[2], w),
                        _id(r[4], w),
                        _id(r[5], w),
                        _id(r[6], w),
                    )

        _copy(cur, "rf2_l_raw", ["id", "et", "active", "refset", "description", "acceptability"], members())  # fmt: skip
    # The latest row per id wins; different rows with the same id and time are refused
    for raw, cols in (("rf2_c_raw", "active, module"), ("rf2_d_raw", "active, concept, type, term"),
                      ("rf2_r_raw", "active, child, parent"),
                      ("rf2_l_raw", "active, refset, description, acceptability")):  # fmt: skip
        clash = _one(cur, f"SELECT count(*) FROM (SELECT id, et FROM {raw} GROUP BY id, et "  # noqa: S608 (constants)
                          f"HAVING count(DISTINCT ({cols})) > 1) x")  # fmt: skip
        if clash:
            msg = f"{clash} components appear twice with the same effectiveTime and different content"
            raise LoadError(msg)
    cur.execute("""
        CREATE TEMP TABLE rf2_concept ON COMMIT DROP AS
          SELECT DISTINCT ON (id) id, active, module FROM rf2_c_raw ORDER BY id, et DESC;
        CREATE TEMP TABLE rf2_desc ON COMMIT DROP AS
          SELECT * FROM (SELECT DISTINCT ON (id) * FROM rf2_d_raw ORDER BY id, et DESC) x WHERE active;
        CREATE TEMP TABLE rf2_isa ON COMMIT DROP AS
          SELECT * FROM (SELECT DISTINCT ON (id) * FROM rf2_r_raw ORDER BY id, et DESC) x WHERE active;
        CREATE TEMP TABLE rf2_lang ON COMMIT DROP AS
          SELECT * FROM (SELECT DISTINCT ON (id) * FROM rf2_l_raw ORDER BY id, et DESC) x WHERE active;
        DROP TABLE rf2_c_raw, rf2_d_raw, rf2_r_raw, rf2_l_raw;
        CREATE UNIQUE INDEX ON rf2_concept (id); CREATE INDEX ON rf2_desc (concept);
        CREATE INDEX ON rf2_lang (description); CREATE INDEX ON rf2_isa (child);
        ANALYZE rf2_concept; ANALYZE rf2_desc; ANALYZE rf2_isa; ANALYZE rf2_lang;
    """)


def _hierarchy(cur):
    """Assign ids, then direct parents and every ancestor, level by level."""
    cur.execute("""
        CREATE TEMP TABLE rf2_id ON COMMIT DROP AS
        SELECT id AS sct, nextval(pg_get_serial_sequence('ihr_terminology_concept', 'id')) AS id FROM rf2_concept
    """)
    cur.execute("""
        CREATE UNIQUE INDEX ON rf2_id (sct); ANALYZE rf2_id;
        CREATE TEMP TABLE rf2_par ON COMMIT DROP AS
        SELECT DISTINCT c.id AS child, p.id AS parent
          FROM rf2_isa i JOIN rf2_id c ON c.sct = i.child JOIN rf2_id p ON p.sct = i.parent;
        CREATE INDEX ON rf2_par (child); ANALYZE rf2_par;
        CREATE TEMP TABLE rf2_anc (child bigint, ancestor bigint, PRIMARY KEY (child, ancestor)) ON COMMIT DROP;
        INSERT INTO rf2_anc SELECT child, parent FROM rf2_par;
        CREATE TEMP TABLE rf2_front ON COMMIT DROP AS SELECT child, parent AS ancestor FROM rf2_par;
    """)
    for _round in range(MAX_ROUNDS):
        cur.execute("""
            CREATE TEMP TABLE rf2_next ON COMMIT DROP AS
            SELECT DISTINCT f.child, p.parent AS ancestor FROM rf2_front f JOIN rf2_par p ON p.child = f.ancestor;
            DELETE FROM rf2_next n USING rf2_anc a WHERE a.child = n.child AND a.ancestor = n.ancestor;
            INSERT INTO rf2_anc SELECT child, ancestor FROM rf2_next;
            DROP TABLE rf2_front;
            ALTER TABLE rf2_next RENAME TO rf2_front;
        """)
        if not _one(cur, "SELECT count(*) FROM rf2_front"):
            break
    else:
        msg = "the is-a hierarchy did not end"
        raise LoadError(msg)
    if _one(cur, "SELECT count(*) FROM rf2_anc WHERE child = ancestor"):
        msg = "a concept is its own ancestor: a cycle in the is-a hierarchy"
        raise LoadError(msg)


def load_release(  # noqa: PLR0912, PLR0915 (one load, step by step)
    paths, *, operator, sha256=(), language_refsets=None, version=None, licence=""
):
    """Load one or more packages as a new active SNOMED version. Returns (version, summary)."""
    if isinstance(paths, (str, Path)):
        paths = [paths]
    if isinstance(sha256, str):
        sha256 = [sha256]
    sha256 = list(sha256 or [])
    if not operator or not str(operator).strip():
        msg = "say who loads it (--operator)"
        raise LoadError(msg)
    throwaway = throwaway_database()
    if not throwaway and (len(sha256) != len(paths) or not licence):
        msg = "outside a bench or test database, give each release's --sha256 and a --licence reference"
        raise LoadError(msg)
    langs = parse_language_refsets(language_refsets)
    english = [r for lang, r in langs if lang == "en"] or [GB_ENGLISH]
    if not any(
        lang == "en" for lang, _ in langs
    ):  # displays come from English: keep GB staged
        langs = [("en", GB_ENGLISH), *langs]
    releases = [
        Release(p, sha256[i] if i < len(sha256) else None) for i, p in enumerate(paths)
    ]
    digests = [r.sha256 for r in releases]
    # The same packages loaded the same way: the files and the language refsets
    key = "+".join(digests) + "|" + ",".join(f"{lang}:{r}" for lang, r in langs)
    record = (
        digests[0]
        if len(digests) == 1 and langs == [("en", GB_ENGLISH)]
        else hashlib.sha256(key.encode()).hexdigest()
    )
    if "folder" not in digests:
        # Unchanged only when that load is the active one; reloading an older release makes it active again
        before = CodeSystemVersion.objects.filter(
            url=SNOMED, sha256=record, status="active"
        ).first()
        if before:
            return before, {"unchanged": True}
    release_version = version or "+".join(r.date for r in releases)
    with transaction.atomic():
        _open_for_writing()
        with connection.cursor() as cur:
            # The load's own memory and time settings, restored at the end (an outer transaction,
            # as in tests, would otherwise keep them)
            names = ("work_mem", "maintenance_work_mem", "statement_timeout")
            cur.execute("SELECT " + ", ".join(f"current_setting('{n}')" for n in names))
            previous = dict(zip(names, cur.fetchone(), strict=True))
            for name, value in (
                ("work_mem", "64MB"),
                ("maintenance_work_mem", "256MB"),
                ("statement_timeout", "0"),
            ):
                cur.execute("SELECT set_config(%s, %s, true)", [name, value])
            cur.execute(
                f"DROP TABLE IF EXISTS {', '.join(TEMP)}"
            )  # an outer transaction (tests) may hold old ones
            _stage(cur, releases, {r for _, r in langs})
            n_concepts = _one(cur, "SELECT count(*) FROM rf2_concept")
            if not n_concepts:
                msg = "no concepts in the release"
                raise LoadError(msg)
            if not throwaway and _one(
                cur,
                "SELECT count(*) FROM rf2_concept WHERE module = %s",
                [SYNTHETIC_MODULE],
            ):
                msg = "this is ihr_terminology_bench's synthetic release: it loads into bench databases only"
                raise LoadError(msg)
            dangling = (
                _one(cur, "SELECT count(*) FROM rf2_desc d WHERE NOT EXISTS "
                          "(SELECT 1 FROM rf2_concept c WHERE c.id = d.concept)"),
                _one(cur, "SELECT count(*) FROM rf2_isa r WHERE NOT EXISTS (SELECT 1 FROM rf2_concept c WHERE c.id = r.child)"
                          " OR NOT EXISTS (SELECT 1 FROM rf2_concept c WHERE c.id = r.parent)"),
            )  # fmt: skip
            if any(dangling):
                msg = (f"{dangling[0]} descriptions and {dangling[1]} is-a rows point at concepts not in this load: "
                       "load an extension together with the edition it depends on (--rf2 edition.zip --rf2 extension.zip)")  # fmt: skip
                raise LoadError(msg)
            v = CodeSystemVersion.objects.create(
                url=SNOMED, version=release_version[:64], name="SNOMED CT", title="SNOMED CT", content="complete",
                licence=licence, source=", ".join(r.path.name for r in releases)[:255], sha256=record[:64],
                concepts=n_concepts, loaded_by=str(operator)[:128], loaded_on_host=socket.gethostname()[:128],
            )  # fmt: skip
            _hierarchy(cur)
            # Display: the preferred synonym of the first English refset that has one, else the FSN
            cur.execute(
                f"""
                CREATE TEMP TABLE rf2_disp ON COMMIT DROP AS
                SELECT DISTINCT ON (d.concept) d.concept, d.term
                  FROM rf2_desc d JOIN rf2_lang l ON l.description = d.id
                 WHERE d.type = {SYNONYM} AND l.acceptability = {PREFERRED} AND l.refset = ANY(%s)
                 ORDER BY d.concept, array_position(%s::bigint[], l.refset), d.id
            """,  # noqa: S608 (constants only)
                [english, english],
            )
            cur.execute(f"""
                INSERT INTO rf2_disp
                SELECT DISTINCT ON (d.concept) d.concept, d.term FROM rf2_desc d
                 WHERE d.type = {FSN} AND NOT EXISTS (SELECT 1 FROM rf2_disp x WHERE x.concept = d.concept)
                 ORDER BY d.concept, d.lang <> 'en', d.id
            """)  # noqa: S608 (constants only)
            cur.execute("CREATE UNIQUE INDEX ON rf2_disp (concept); ANALYZE rf2_disp")
            cur.execute(
                """
                INSERT INTO ihr_terminology_concept (id, system_id, code, display, active, parents, ancestors, properties)
                SELECT i.id, %s, c.id::text, coalesce(x.term, c.id::text), c.active,
                       coalesce(p.ids, '{}'), coalesce(a.ids, '{}'), jsonb_build_object('module', c.module::text)
                  FROM rf2_concept c JOIN rf2_id i ON i.sct = c.id
                  LEFT JOIN rf2_disp x ON x.concept = c.id
                  LEFT JOIN (SELECT child, array_agg(parent ORDER BY parent) ids FROM rf2_par GROUP BY child) p
                         ON p.child = i.id
                  LEFT JOIN (SELECT child, array_agg(ancestor ORDER BY ancestor) ids FROM rf2_anc GROUP BY child) a
                         ON a.child = i.id
            """,
                [v.id],
            )
            # Designations: every active description; preferred = a synonym preferred by a refset
            # mapped to the description's own language
            pairs = [f"{lang}:{r}" for lang, r in langs]
            cur.execute(
                f"""
                INSERT INTO ihr_terminology_designation
                       (concept_id, language, use_system, use_code, value, preferred, search_text)
                SELECT i.id, d.lang, %s, d.type::text, d.term,
                       d.type = {SYNONYM} AND EXISTS (
                         SELECT 1 FROM rf2_lang l WHERE l.description = d.id AND l.acceptability = {PREFERRED}
                            AND (d.lang || ':' || l.refset) = ANY(%s)),
                       d.search
                  FROM rf2_desc d JOIN rf2_id i ON i.sct = d.concept
            """,  # noqa: S608 (constants only)
                [SNOMED, pairs],
            )
            if (
                _one(
                    cur,
                    "SELECT count(*) FROM ihr_terminology_concept WHERE system_id = %s",
                    [v.id],
                )
                != n_concepts
            ):
                msg = "loaded concept count does not match the release"
                raise LoadError(msg)
            cur.execute(
                "SELECT module, count(*) FROM rf2_concept GROUP BY module ORDER BY module"
            )
            summary = {
                "concepts": n_concepts,
                "modules": {str(m): n for m, n in cur.fetchall()},
                "descriptions": _one(cur, "SELECT count(*) FROM rf2_desc"),
                "is_a": _one(cur, "SELECT count(*) FROM rf2_par"),
                "ancestor_pairs": _one(cur, "SELECT count(*) FROM rf2_anc"),
            }
            cur.execute(f"DROP TABLE IF EXISTS {', '.join(TEMP)}")
            # Planner statistics now, not whenever autovacuum gets to it
            cur.execute("ANALYZE ihr_terminology_concept, ihr_terminology_designation")
            for name, value in previous.items():
                cur.execute("SELECT set_config(%s, %s, true)", [name, value])
        CodeSystemVersion.objects.filter(url=SNOMED, status="active").update(
            status="retired"
        )
        v.status = "active"
        v.save(update_fields=["status"])
        _close_for_writing()
    log.warning("terminology: SNOMED %s loaded: %s", release_version, summary)
    return v, summary
