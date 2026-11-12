"""Loads terminology content (P1-T10a1): FHIR CodeSystem resources, and FHIR ValueSets that
list their codes (a fragment of a code system: UCUM's common units).

Every load is a new version: rows are written as ``loading``, checked, then switched to ``active``
in one transaction while the previous version becomes ``retired`` (kept until ``prune``). The
tables refuse writes unless the loader has opened them for its own transaction (migration 0002),
and only in ``public``. The version row records the file, its sha256, the operator and the host.
"""

import hashlib
import json
import socket
from pathlib import Path

from django.db import connection, transaction

from ihr_terminology.models import CodeSystemVersion, Concept, Designation
from ihr_terminology.text import normalise

CONTENT = Path(__file__).with_name("content")
PARENT_PROPERTIES = {"parent", "subsumedBy"}
MAX_FILE_BYTES = (
    64 * 2**20
)  # FHIR JSON read whole; bigger content (SNOMED, LOINC) uses RF2/CSV loaders (P1-T10a2)


class LoadError(Exception):
    pass


def _open_for_writing():
    if getattr(connection, "schema_name", "public") != "public":
        msg = "terminology is loaded in the public schema only (not from a hospital's context)"
        raise LoadError(msg)
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('ihr.terminology_load', 'on', true)")


def _close_for_writing():
    # set_config(..., true) lasts until the outermost transaction ends: when the loader runs
    # inside a caller's transaction (tests, a future task), close the tables again ourselves
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('ihr.terminology_load', 'off', true)")


def read_verified(path, sha256=None):
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        msg = f"{path.name}: larger than {MAX_FILE_BYTES // 2**20} MB"
        raise LoadError(msg)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if sha256 and digest != sha256:
        msg = f"{path.name}: sha256 {digest} is not the expected {sha256}"
        raise LoadError(msg)
    try:
        return json.loads(raw), digest
    except ValueError as e:
        msg = f"{path.name}: not JSON ({e})"
        raise LoadError(msg) from e


def _walk(concepts, parent_code, out):
    for c in concepts or []:
        if (
            not isinstance(c, dict)
            or not isinstance(c.get("code"), str)
            or not c["code"]
        ):
            continue
        parents = set()
        if parent_code:
            parents.add(parent_code)
        for prop in c.get("property") or []:
            if prop.get("code") in PARENT_PROPERTIES and isinstance(
                prop.get("valueCode"), str
            ):
                parents.add(prop["valueCode"])
        inactive = any(
            prop.get("code") in ("inactive", "status")
            and prop.get("valueBoolean", prop.get("valueCode"))
            in (True, "retired", "inactive")
            for prop in c.get("property") or []
        )
        out.append(
            {
                "code": c["code"],
                "display": c.get("display") or c["code"],
                "parents": parents,
                "designations": c.get("designation") or [],
                "active": not inactive,
            }
        )
        _walk(c.get("concept"), c["code"], out)


def _from_resource(res):
    """A CodeSystem or an enumerating ValueSet -> (url, version, name, title, content, rows)."""
    kind = res.get("resourceType")
    rows = []
    if kind == "CodeSystem":
        _walk(res.get("concept"), None, rows)
        return (
            res.get("url"),
            res.get("version") or "",
            res.get("name") or "",
            res.get("title") or "",
            res.get("content") or "complete",
            rows,
        )
    if kind == "ValueSet":
        includes = ((res.get("compose") or {}).get("include")) or []
        if (
            len(includes) != 1
            or not includes[0].get("concept")
            or includes[0].get("filter")
        ):
            msg = "a ValueSet is loaded only when it lists the codes of one system"
            raise LoadError(msg)
        inc = includes[0]
        for c in inc["concept"]:
            if isinstance(c.get("code"), str) and c["code"]:
                rows.append(
                    {
                        "code": c["code"],
                        "display": c.get("display") or c["code"],
                        "parents": set(),
                        "designations": c.get("designation") or [],
                        "active": True,
                    }
                )
        version = f"{res.get('version') or ''}:{res.get('id') or res.get('name') or 'valueset'}"
        return (
            inc.get("system"),
            version,
            res.get("name") or "",
            res.get("title") or "",
            "fragment",
            rows,
        )
    msg = f"not a CodeSystem or ValueSet: {kind}"
    raise LoadError(msg)


def _merge_duplicates(rows):
    """FHIR's ucum-common lists 7 codes twice: one concept, the second display as a synonym."""
    merged = {}
    for r in rows:
        if r["code"] in merged:
            m = merged[r["code"]]
            if r["display"] != m["display"]:
                m["designations"].append({"language": "en", "value": r["display"]})
            m["parents"] |= r["parents"]
        else:
            merged[r["code"]] = {**r, "designations": list(r["designations"])}
    return list(merged.values())


def _ancestors(parents_by_code):
    memo, visiting = {}, set()

    def anc(code):
        if code in memo:
            return memo[code]
        if code in visiting:
            msg = f"a cycle in the hierarchy at {code}"
            raise LoadError(msg)
        visiting.add(code)
        out = set()
        for p in parents_by_code.get(code, ()):
            if p in parents_by_code:
                out.add(p)
                out |= anc(p)
        visiting.discard(code)
        memo[code] = out
        return out

    return {code: anc(code) for code in parents_by_code}


def load_resource(res, *, source, sha256, operator, licence=""):
    """Load one resource as a new active version. Returns the CodeSystemVersion (or the existing
    active one when the same url, version and sha256 are already active)."""
    if not operator or not str(operator).strip():
        msg = "say who loads it (--operator)"
        raise LoadError(msg)
    url, version, name, title, content, rows = _from_resource(res)
    if not isinstance(url, str) or not url:
        msg = "the resource has no system url"
        raise LoadError(msg)
    rows = _merge_duplicates(rows)
    if not rows:
        msg = f"{url}: no codes"
        raise LoadError(msg)
    same = CodeSystemVersion.objects.filter(
        url=url, version=version, sha256=sha256, status="active"
    ).first()
    if same:
        return same
    parents_by_code = {r["code"]: r["parents"] for r in rows}
    ancestors = _ancestors(parents_by_code)
    with transaction.atomic():
        _open_for_writing()
        v = CodeSystemVersion.objects.create(
            url=url,
            version=version,
            name=name[:255],
            title=title[:255],
            content="fragment" if content in ("fragment", "example") else "complete",
            licence=licence,
            source=str(source)[:255],
            sha256=sha256,
            concepts=len(rows),
            loaded_by=str(operator)[:128],
            loaded_on_host=socket.gethostname()[:128],
        )
        objs = Concept.objects.bulk_create(
            [
                Concept(
                    system=v, code=r["code"], display=r["display"], active=r["active"]
                )
                for r in rows
            ],
            batch_size=2000,
        )
        ids = {o.code: o.id for o in objs}
        for o in objs:
            o.parents = sorted(ids[p] for p in parents_by_code[o.code] if p in ids)
            o.ancestors = sorted(ids[a] for a in ancestors[o.code])
        Concept.objects.bulk_update(objs, ["parents", "ancestors"], batch_size=2000)
        des = []
        for r in rows:
            cid = ids[r["code"]]
            des.append(
                Designation(
                    concept_id=cid,
                    language="en",
                    value=r["display"],
                    preferred=True,
                    # the term only: codes are matched by the search's exact-code path
                    search_text=normalise(r["display"]),
                )
            )
            for d in r["designations"]:
                value = d.get("value") if isinstance(d, dict) else None
                if not isinstance(value, str) or not value or value == r["display"]:
                    continue
                use = d.get("use") or {}
                des.append(
                    Designation(
                        concept_id=cid,
                        language=(d.get("language") or "en")[:16],
                        value=value,
                        use_system=(use.get("system") or "")[:255],
                        use_code=(use.get("code") or "")[:64],
                        # the preferred term in its language (FHIR's designation-usage code)
                        preferred=use.get("code") == "preferredForLanguage",
                        search_text=normalise(value),
                    )
                )
        Designation.objects.bulk_create(des, batch_size=2000)
        if Concept.objects.filter(system=v).count() != len(rows):
            msg = f"{url}: loaded count does not match"
            raise LoadError(msg)
        CodeSystemVersion.objects.filter(url=url, status="active").update(
            status="retired"
        )
        v.status = "active"
        v.save(update_fields=["status"])
        with (
            connection.cursor() as cur
        ):  # planner statistics now, not whenever autovacuum runs
            cur.execute("ANALYZE ihr_terminology_concept, ihr_terminology_designation")
        _close_for_writing()
    return v


def load_file(path, *, sha256=None, operator, licence=""):
    res, digest = read_verified(path, sha256)
    return load_resource(
        res, source=Path(path).name, sha256=digest, operator=operator, licence=licence
    )


def load_bundled(*, operator):
    """Everything in content/, each file checked against MANIFEST.json first. A file is loaded
    only if this exact content (url and sha256) was never loaded: so a newer version an operator
    loaded with --fhir is never replaced by the older bundled one on the next `up`.
    Returns [(version, newly_loaded)]."""
    manifest = json.loads((CONTENT / "MANIFEST.json").read_text())
    licences = {
        s["folder"]: f"{s['package']} {s['version']}, {s['licence']}"
        for s in manifest["sources"]
    }
    on_disk = {
        str(p.relative_to(CONTENT))
        for p in CONTENT.rglob("*.json")
        if p.name != "MANIFEST.json"
    }
    if on_disk != set(manifest["files"]):
        msg = f"content/ differs from MANIFEST.json: {sorted(on_disk ^ set(manifest['files']))}"
        raise LoadError(msg)
    loaded = []
    for rel, sha in sorted(manifest["files"].items()):
        res, digest = read_verified(CONTENT / rel, sha)
        url = (
            res.get("url")
            if res.get("resourceType") == "CodeSystem"
            else (((res.get("compose") or {}).get("include") or [{}])[0].get("system"))
        )
        before = (
            CodeSystemVersion.objects.filter(url=url, sha256=digest)
            .order_by("-loaded_at")
            .first()
        )
        if before:
            loaded.append((before, False))
            continue
        v = load_resource(
            res,
            source=rel,
            sha256=digest,
            operator=operator,
            licence=licences[rel.split("/")[0]],
        )
        loaded.append((v, True))
    return loaded


def prune(*, operator):
    """Remove the concepts of retired versions; their version rows stay as the load history.
    Plain SQL: Django's delete collector would load a whole SNOMED version into memory."""
    if not operator:
        msg = "say who prunes (--operator)"
        raise LoadError(msg)
    with transaction.atomic():
        _open_for_writing()
        retired = list(
            CodeSystemVersion.objects.filter(status="retired").values_list(
                "id", flat=True
            )
        )
        if not retired:
            _close_for_writing()
            return 0
        with connection.cursor() as cur:
            cur.execute(
                "DELETE FROM ihr_terminology_designation d USING ihr_terminology_concept c"
                " WHERE c.id = d.concept_id AND c.system_id = ANY(%s)",
                [retired],
            )
            cur.execute(
                "DELETE FROM ihr_terminology_concept WHERE system_id = ANY(%s)",
                [retired],
            )
            n = cur.rowcount
        CodeSystemVersion.objects.filter(id__in=retired).update(concepts=0)
        with connection.cursor() as cur:
            cur.execute("ANALYZE ihr_terminology_concept, ihr_terminology_designation")
        _close_for_writing()
    return n
