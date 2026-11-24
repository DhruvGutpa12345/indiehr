#!/usr/bin/env python3
"""The batch allow-list (P1-T20a), generated from the web app's own batch call sites.

The backend runs a sub-request of ``POST /api/v1/batch_requests/`` only if its method and route
are in plugins/ihr_platform/ihr_platform/batch_allowlist.txt (ihr_platform/batch.py). This script
writes that list from apps/frontend/src: every ``api:`` of a batch request object in a file that
uses the batch helpers (``@/Utils/request/batch``), resolved through the ``*Api.ts`` module it
is imported from to its exact path and method. Each entry names its call sites (file and Api
symbol, no line numbers, so a moved line is not drift).

Usage:
  scripts/batch-allowlist.py          # write the list
  scripts/batch-allowlist.py --check  # fail when the list is not what the web app batches
                                      # (pre-commit, Apps CI, after every upstream sync)

Fails closed: an ``api:`` it cannot resolve (a variable, a helper's return value) must have a
reviewed entry in DYNAMIC below, pinned to the file's code as reviewed; a patient-OTP route (``/api/v1/otp/``) or the batch endpoint
itself never belongs in a staff batch; any other code that posts to the batch endpoint without
the helpers is refused. Stdlib only.
"""

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "apps" / "frontend" / "src"
OUT = ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "batch_allowlist.txt"
HELPERS = "Utils/request/batch.ts"
IMPORTS_HELPERS = re.compile(r"""from\s+["']@/Utils/request/batch["']""")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# Call sites whose ``api:`` is a variable, resolved by hand (reviewed). The check fails if the
# file no longer uses that variable, uses it more than once, no longer names a listed route, has
# another variable, or changed at all since the review: ``sha256`` is site_hash() of the file as
# reviewed (comments and spacing aside), so any edit near the dynamic ``api:`` forces a new
# review (review C3). The check prints the new value to set once the file is re-reviewed.
DYNAMIC = {
    # handleAddOrganizations batches getMutationParams(entityType, ..., true): only the "add"
    # routes. The removes go through mutate(), one call each, never in a batch.
    # CARE 3.1 questionnaire manager: one batch of the update plus either the facility or the
    # hospital-wide organisation route (api: facility ? A : B).
    ("components/QuestionnaireV2/manage/useUpdateQuestionnaire.ts", "facility"): {
        "routes": (
            "questionnaireApi.setFacilityOrganizations",
            "questionnaireApi.setOrganizations",
        ),
        "sha256": "1dadd21d2a45548e43a0d2d294a466f243a76681d5271f7a03ddf7dc16478ea1",
    },
    ("components/Patient/LinkDepartmentsSheet.tsx", "route"): {
        "routes": (
            "encounterApi.addOrganization",
            "locationApi.addOrganization",
            "deviceApi.addOrganization",
        ),
        "sha256": "ee8dde0e46790866a3a2224fe8cee681e266855a5d9049ecf9bafb23ecbdd3a7",
    },
}

# Raw-URL batch entries (CARE 3.1 questionnaire fill, P2-T0b). The fill page sends its batch from
# useSubmitFillSession.ts as ``api: { path: request.url, ... }``; each request's url and method
# come from URL templates in the files below (structured/definitions/*.ts, submitTarget.ts,
# composeBatch.ts), never from an Api symbol. Reviewed table: (METHOD, route, file, template as
# written in that file). The check fails if a template is gone or changed, and if any other
# `/api/v1/...` template literal appears in RAW_DIRS (a new structured type or a new request
# must be reviewed into this table; a new line is a route a staff batch may run).
RAW_SENDER = "components/QuestionnaireV2/fill/submit/useSubmitFillSession.ts"
RAW_DIRS = (
    "components/QuestionnaireV2/structured",
    "components/QuestionnaireV2/fill/submit",
)
_D = "components/QuestionnaireV2/structured/definitions/"
_S = "components/QuestionnaireV2/fill/submit/"
RAW_ROUTES = (
    ("POST", "/api/v1/patient/{patientId}/allergy_intolerance/upsert/", _D + "allergyIntolerance.ts",
     "`/api/v1/patient/${patientId}/allergy_intolerance/upsert/`"),
    ("POST", "/api/v1/facility/{facilityId}/slots/{slotId}/create_appointment/", _D + "appointment.ts",
     "`/api/v1/facility/${facilityId}/slots/${slot_id}/create_appointment/`"),
    ("POST", "/api/v1/facility/{facilityId}/charge_item/apply_charge_item_defs/", _D + "chargeItem.ts",
     "`/api/v1/facility/${facilityId}/charge_item/apply_charge_item_defs/`"),
    ("POST", "/api/v1/patient/{patientId}/diagnosis/upsert/", _D + "diagnosis.ts",
     "`/api/v1/patient/${patientId}/diagnosis/upsert/`"),
    ("PUT", "/api/v1/encounter/{id}/", _D + "encounter.ts",
     "`/api/v1/encounter/${encounterId}/`"),
    ("POST", "/api/v1/files/upload-file/", _D + "files.ts",
     "`/api/v1/files/upload-file/`"),
    ("POST", "/api/v1/patient/{patientId}/medication/request/upsert/", _D + "medicationRequest.ts",
     "`/api/v1/patient/${patientId}/medication/request/upsert/`"),
    ("POST", "/api/v1/patient/{patientId}/medication/statement/upsert/", _D + "medicationStatement.ts",
     "`/api/v1/patient/${patientId}/medication/statement/upsert/`"),
    ("POST", "/api/v1/patient/{patientId}/procedure/upsert/", _D + "procedure.ts",
     "`/api/v1/patient/${patientId}/procedure/upsert/`"),
    ("POST", "/api/v1/facility/{facilityId}/service_request/apply_activity_definition/", _D + "serviceRequest.ts",
     "`/api/v1/facility/${facilityId}/service_request/apply_activity_definition/`"),
    ("POST", "/api/v1/patient/{patientId}/symptom/upsert/", _D + "symptom.ts",
     "`/api/v1/patient/${patientId}/symptom/upsert/`"),
    ("PUT", "/api/v1/patient/{id}/", _D + "timeOfDeath.ts",
     "`/api/v1/patient/${patientId}/`"),
    ("POST", "/api/v1/questionnaire/{id}/submit/", _S + "submitTarget.ts",
     "`/api/v1/questionnaire/${questionnaireId}/submit/`"),
    ("POST", "/api/v1/questionnaire/{id}/submit_resource/", _S + "submitTarget.ts",
     "`/api/v1/questionnaire/${questionnaireId}/submit_resource/`"),
    ("PUT", "/api/v1/form_submission/{id}/", _S + "composeBatch.ts",
     "`/api/v1/form_submission/${continueDraftId}/`"),
)
RAW_TEMPLATE = re.compile(r"`/api/v1/[^`]*`")


def raw_entries():
    """({(METHOD, route): {site}}, problems) for the reviewed raw-URL table."""
    entries, problems, listed = {}, [], {}
    for method, route, rel, template in RAW_ROUTES:
        listed.setdefault(rel, set()).add(template)
        file = SRC / rel
        if not file.is_file():
            problems.append(f"{rel}: raw batch url file is gone (RAW_ROUTES lists {route})")
        elif template not in strip_comments(file.read_text()):
            problems.append(f"{rel}: raw batch url {template} is gone or changed (RAW_ROUTES)")
        else:
            entries.setdefault((method, route), set()).add(f"{rel}: raw url")
    for directory in RAW_DIRS:
        for path in sorted((SRC / directory).rglob("*.ts*")):
            rel = path.relative_to(SRC).as_posix()
            for template in RAW_TEMPLATE.findall(strip_comments(path.read_text())):
                if template not in listed.get(rel, ()):
                    problems.append(
                        f"{rel}: url {template} is not in RAW_ROUTES in scripts/batch-allowlist.py; "
                        "review whether a batch may send it and add it"
                    )
    return entries, problems


HEADER = """\
# Batch allow-list (P1-T20a): every method and route the web app sends inside a batch request
# (POST /api/v1/batch_requests/). The batch view refuses any other sub-request, and the whole
# batch with it (ihr_platform/batch.py). GENERATED by scripts/batch-allowlist.py from the call
# sites in apps/frontend/src: do not edit by hand. Change the web app or the script's reviewed
# DYNAMIC table, run the script, and review the diff.
# One entry per line: METHOD PATH  # its call sites (file: Api symbol)
"""


class Problem(Exception):
    pass


def strip_comments(text):
    """Text with // and /* */ comments blanked (strings and template literals kept)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            out.append(" ")
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def site_hash(text):
    """sha256 of a file's code: comments blanked, runs of whitespace made one space."""
    return hashlib.sha256(" ".join(strip_comments(text).split()).encode()).hexdigest()


def _skip_ws(text, i):
    while i < len(text) and text[i].isspace():
        i += 1
    return i


def _value_end(text, i):
    """End of one property value starting at ``i``: the next , or } at its own depth."""
    depth, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c in "([{" or (c == "<" and i and (text[i - 1].isalnum() or text[i - 1] == "_")):
            depth += 1
        elif c in ")]}" or (c == ">" and depth and text[i - 1] != "="):
            if depth == 0:
                return i
            depth -= 1
        elif c == "," and depth == 0:
            return i
        i += 1
    raise Problem("unterminated value")


KEY = re.compile(r"""(?:([A-Za-z_$][\w$]*)|"([^"]*)"|'([^']*)')\s*:""")


def parse_object(text, i):
    """Parse the object literal whose ``{`` is at ``i``: ({key: dict | raw value}, end)."""
    assert text[i] == "{"
    obj, i = {}, i + 1
    while True:
        i = _skip_ws(text, i)
        if i >= len(text):
            raise Problem("unterminated object")
        if text[i] == "}":
            return obj, i + 1
        if text[i] == ",":
            i += 1
            continue
        if text.startswith("...", i):  # a spread: skip it
            i = _value_end(text, i + 3)
            continue
        m = KEY.match(text, i)
        if not m:  # shorthand property or method: skip it
            i = _value_end(text, i)
            continue
        key = m.group(1) or m.group(2) or m.group(3)
        i = _skip_ws(text, m.end())
        if text[i] == "{":
            obj[key], i = parse_object(text, i)
        else:
            end = _value_end(text, i)
            obj[key] = text[i:end].strip()
            i = end


def default_export(module):
    text = strip_comments(module.read_text())
    m = re.search(r"export\s+default\s+\{", text)
    if not m:
        name = re.search(r"export\s+default\s+([A-Za-z_$][\w$]*)", text)
        if name:
            m = re.search(
                r"(?:const|let|var)\s+%s\b[^=]*=\s*\{" % re.escape(name.group(1)), text
            )
    if not m:
        raise Problem(f"{module.relative_to(SRC)}: no default-exported object")
    obj, _end = parse_object(text, m.end() - 1)
    return obj


def resolve_module(spec, importer):
    if spec.startswith("@/"):
        base = SRC / spec[2:]
    elif spec.startswith("."):
        base = (importer.parent / spec).resolve()
    else:
        raise Problem(f"{importer.relative_to(SRC)}: cannot follow import {spec!r}")
    for candidate in (
        base.with_name(base.name + ".ts"),
        base.with_name(base.name + ".tsx"),
        base / "index.ts",
    ):
        if candidate.is_file():
            return candidate
    raise Problem(f"{importer.relative_to(SRC)}: import {spec!r} not found")


def route_of(symbol, importer, text):
    """(METHOD, path) of an Api symbol such as ``scheduleApis.slots.createAppointment``."""
    base, *members = symbol.split(".")
    m = re.search(
        r"""import\s+%s\s+from\s+["']([^"']+)["']""" % re.escape(base), text
    )
    if not m:
        raise Problem(f"{importer.relative_to(SRC)}: {base} is not a default import")
    node = default_export(resolve_module(m.group(1), importer))
    for member in members:
        if not isinstance(node, dict) or member not in node:
            raise Problem(f"{importer.relative_to(SRC)}: {symbol} not found")
        node = node[member]
    if not isinstance(node, dict) or "path" not in node:
        raise Problem(f"{importer.relative_to(SRC)}: {symbol} is not an API route")
    path = re.fullmatch(r"""(["'`])([^"'`$]*)\1""", node["path"])
    if not path:
        raise Problem(f"{symbol}: path is not a plain string ({node['path']})")
    method = node.get("method", "HttpMethod.GET")  # batch.ts: method ?? GET
    verb = re.fullmatch(r"HttpMethod\.([A-Z]+)", method)
    if not verb or verb.group(1) not in METHODS:
        raise Problem(f"{symbol}: method {method!r} is not one of {METHODS}")
    return verb.group(1), path.group(2)


API_VALUE = re.compile(r"\bapi\s*:\s*([^,}\n]+)")
SYMBOL = re.compile(r"\b[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+")
BARE = re.compile(r"[A-Za-z_$][\w$]*")


def call_sites():
    """{(METHOD, path): {"file: symbol"}} for every batched ``api:`` in the web app."""
    entries, problems, dynamic_seen = {}, [], set()
    for path in sorted(SRC.rglob("*.ts*")):
        rel = path.relative_to(SRC).as_posix()
        if path.suffix not in (".ts", ".tsx") or rel == HELPERS:
            continue
        raw = path.read_text()
        if not IMPORTS_HELPERS.search(raw):
            if "batch_requests" in strip_comments(raw):
                problems.append(f"{rel}: posts to the batch endpoint without the helpers")
            continue
        text = strip_comments(raw)
        for m in API_VALUE.finditer(text):
            value = m.group(1).strip()
            symbols = SYMBOL.findall(value)
            if not symbols:
                key = (rel, value)
                if rel == RAW_SENDER and value == "{":
                    continue  # its routes come from RAW_ROUTES, below
                if BARE.fullmatch(value) and key in DYNAMIC:
                    reviewed = DYNAMIC[key]
                    symbols = list(reviewed["routes"])
                    if key in dynamic_seen:
                        continue  # judged at its first use (more than one is refused there)
                    dynamic_seen.add(key)
                    uses = [
                        u for u in API_VALUE.finditer(text) if u.group(1).strip() == value
                    ]
                    if len(uses) != 1:
                        problems.append(
                            f"{rel}: api: {value} is used {len(uses)} times; DYNAMIC "
                            "reviewed one call site"
                        )
                    if (digest := site_hash(raw)) != reviewed["sha256"]:
                        problems.append(
                            f"{rel}: DYNAMIC site changed since its review; re-review which "
                            f"routes api: {value} can name, then set its sha256 to {digest}"
                        )
                    for symbol in symbols:
                        if not re.search(r"\b%s\b" % re.escape(symbol), text):
                            problems.append(f"{rel}: DYNAMIC route {symbol} no longer there")
                else:
                    problems.append(
                        f"{rel}: batch api {value!r} cannot be resolved; review it and add "
                        "it to DYNAMIC in scripts/batch-allowlist.py"
                    )
                    continue
            for symbol in symbols:
                try:
                    method, route = route_of(symbol, path, text)
                except Problem as exc:
                    problems.append(str(exc))
                    continue
                if route.startswith("/api/v1/otp/"):
                    problems.append(f"{rel}: {symbol} is a patient-OTP route in a staff batch")
                elif route.startswith("/api/v1/batch_requests"):
                    problems.append(f"{rel}: {symbol} nests a batch")
                elif not route.startswith("/api/v1/"):
                    problems.append(f"{rel}: {symbol} is not an /api/v1/ route ({route})")
                else:
                    entries.setdefault((method, route), set()).add(f"{rel}: {symbol}")
    for key in DYNAMIC.keys() - dynamic_seen:
        problems.append(f"{key[0]}: DYNAMIC entry for api: {key[1]} is no longer used")
    raw, raw_problems = raw_entries()
    problems += raw_problems
    for route, sites in raw.items():
        entries.setdefault(route, set()).update(sites)
    sender = SRC / RAW_SENDER
    if RAW_ROUTES and (
        not sender.is_file() or "path: request.url" not in strip_comments(sender.read_text())
    ):
        problems.append(f"{RAW_SENDER}: no longer sends `api: {{ path: request.url }}`; re-review RAW_ROUTES")
    return entries, problems


def render(entries):
    lines = [HEADER]
    for (method, route), sites in sorted(entries.items(), key=lambda e: (e[0][1], e[0][0])):
        lines.append(f"{method} {route}  # {'; '.join(sorted(sites))}\n")
    return "".join(lines)


def main(argv):
    check = argv[1:] == ["--check"]
    if argv[1:] not in ([], ["--check"]):
        sys.exit(__doc__)
    entries, problems = call_sites()
    if problems:
        print("batch-allowlist: " + "\n  ".join(["refused:", *problems]), file=sys.stderr)
        return 1
    text = render(entries)
    if check:
        current = OUT.read_text() if OUT.is_file() else ""
        if current != text:
            print(
                f"batch-allowlist: {OUT.relative_to(ROOT)} is not what the web app batches. "
                "Run scripts/batch-allowlist.py, review the diff (every new route is one a "
                "staff batch may run), and commit it.",
                file=sys.stderr,
            )
            return 1
        print(f"batch-allowlist: {len(entries)} routes, list current")
        return 0
    OUT.write_text(text)
    print(f"batch-allowlist: wrote {len(entries)} routes to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
