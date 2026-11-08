#!/usr/bin/env python3
"""Check that every date display pattern in the web app follows the owner's India style (P1-T7b).

The web app rewrites display patterns at run time through two explicit tables in
apps/frontend/src/Utils/ihr/indiaDateFormats.ts (one for date-fns, one for dayjs). A pattern
missing from those tables is shown exactly as upstream wrote it (e.g. "Nov 8, 2026"). This
script finds every string literal in apps/frontend/src made only of date tokens and fails when
one is in neither table (as a key or a result) nor in the reviewed allow-list below.

Typical cause: an upstream sync added a new display format. Fix: add it to the right table in
indiaDateFormats.ts (and a test), or, if it is an API/input format or has no year, to ALLOWED
here with the reason. "HH:mm" / "HH:mm:ss" (24-hour) are allowed only in the files listed in
PATH_ALLOWED, where they are API values or parsing; a display must use 12-hour "hh:mm a".

A second scan finds calls that format dates by the browser's language or by date-fns' en-US
locale instead of a pattern: .toLocaleString( / .toLocaleDateString( / .toLocaleTimeString(,
Intl.DateTimeFormat( and formatRelative(. Each file with one must be in LOCALE_CALLS_ALLOWED
with a reviewed reason; a full date or time shown to staff must use Utils/ihr/indiaDateFormats.ts
(indiaDateTime etc.) instead. An allow-list entry whose file no longer has such a call fails too.

Usage: python3 scripts/check-date-formats.py [--root apps/frontend/src]
Exit status 0 = all known, 1 = unknown patterns (listed with file:line).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO / "apps/frontend/src"
MAP_FILE = "Utils/ihr/indiaDateFormats.ts"
# Our own files hold the tables and their tests; they are the reference, not the subject.
SKIP_DIRS = ("Utils/ihr/",)

# Formats that must NOT be rewritten, with the reason. Reviewed 2026-11-08 (P1-T7b).
ALLOWED: dict[str, str] = {
    "yyyy-MM-dd": "API and query-string date (date-fns)",
    "YYYY-MM-DD": "API and query-string date (dayjs), date-of-birth check",
    "yyyy-MM-dd'T'HH:mm": "value of datetime-local inputs and their min/max",
    "yyyy": "year only",
    "dd MMM": "no year (owner decision 1)",
    "d MMM": "no year: date-range label within one year",
    "EEE, dd MMM": "no year (owner decision 1)",
    "do MMMM": "no year (owner decision 1)",
    "MMM dd": "no year (owner decision 1)",
    "dd/MM": "no year: medication chart column heading",
    "MM/yyyy": "month and year only: expiry (owner decision 1)",
    "MMM yyyy": "month and year only: expiry (owner decision 1)",
    "EEE": "weekday only",
    "EEEE": "weekday only",
}

# 24-hour patterns: allowed only in these files (paths under the root), where they are API values
# or parsing, never display. Reviewed 2026-11-08 (P1-T7b).
_SCHEDULE_SHEETS = (
    "pages/Scheduling/components/CreateScheduleExceptionSheet.tsx",
    "pages/Scheduling/components/CreateScheduleTemplateSheet.tsx",
    "pages/Scheduling/components/EditScheduleTemplateSheet.tsx",
)
PATH_ALLOWED: dict[str, tuple[str, ...]] = {
    # utils.ts: slot start/end sent to the API as Time "HH:mm"; parse() of API times.
    # The sheets: dayjs(data.start_time, "HH:mm") parses the form's time values.
    "HH:mm": ("pages/Scheduling/utils.ts", *_SCHEDULE_SHEETS),
    # utils.ts: parse() of the API's "HH:mm:ss" availability and exception times.
    "HH:mm:ss": ("pages/Scheduling/utils.ts",),
}

# Files allowed to call toLocale*String / Intl.DateTimeFormat / formatRelative, with the reason.
# Reviewed 2026-11-08 (P1-T7b).
LOCALE_CALLS_ALLOWED: dict[str, str] = {
    "components/Encounter/CreateEncounterForm.tsx": (
        "value of an <input type=time> (hour12: false gives the HH:mm the input needs), not display"
    ),
    "components/Questionnaire/QuestionTypes/EncounterQuestion.tsx": (
        "value of an <input type=time> (hour12: false gives the HH:mm the input needs), not display"
    ),
    "components/ui/calendar.tsx": "month dropdown of the date picker: month name only, no date",
    "CAREUI/interactive/Calendar.tsx": "calendar heading: month and year only (owner decision 1)",
    "components/Notes/NoteManager.tsx": (
        "formatRelative from date-fns goes through our shim Utils/ihr/indiaDateFns.ts "
        "(DD-MM-YYYY, 12-hour), no locale passed"
    ),
}
LOCALE_CALL = re.compile(
    r"\.toLocale(?:Date|Time)?String\(|\bIntl\.DateTimeFormat\(|\bformatRelative\("
)

# String literals that look like date tokens but are not dates.
NOT_DATES: dict[str, str] = {
    "PM": "medication frequency code (evening)",
    "Apps": "navigation label",
    "hh": "encounter class code (home health)",
    "DD": "date-of-birth box placeholder",
    "MM": "date-of-birth box placeholder",
    "YYYY": "date-of-birth box placeholder",
    "hh:mm": "placeholder of our time box (components/ihr/IndiaDateTimeInput.tsx)",
    "add": "a verb",
}

_TOKEN = (
    r"(?:yyyy|YYYY|yy|YY|MMMM|MMM|MM|M|Do|do|dddd|ddd|dd|DD|d|D|EEEE|EEE|EE|E|"
    r"eeee|eee|HH|H|hh|h|mm|m|ss|s|SSS|A|aa|a|PPPP|PPP|PP|P|pp|p|zzz|zz|z|Z)"
)
_SEP = r"(?:[ ,:/\-\.;]+|'[^']*'|\[[^\]]*\])"
PATTERN = re.compile(rf"^{_SEP}*{_TOKEN}(?:{_SEP}*{_TOKEN})*{_SEP}*$")
# At least one token that only a date pattern would have (keeps words like "a", "s" out).
STRONG = re.compile(r"yyyy|YYYY|yy|YY|MMM|MM|dd|DD|HH|hh|h:mm|EEE|ddd|P|pp")
LITERAL = re.compile(r'"([^"\\\n]{1,60})"|\'([^\'\\\n]{1,60})\'|`([^`$\\\n]{1,60})`')
MAP_ENTRY = re.compile(r'^\s*"([^"]+)":\s*"([^"]*)",\s*$', re.M)


def load_tables(map_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """The date-fns and dayjs tables from indiaDateFormats.ts."""
    text = map_path.read_text(encoding="utf-8")
    tables = []
    for name in ("DATE_FNS_FORMAT_MAP", "DAYJS_FORMAT_MAP"):
        start = text.index(f"export const {name}")
        body = text[start : text.index("};", start)]
        tables.append(dict(MAP_ENTRY.findall(body)))
    if not tables[0] or not tables[1]:
        raise SystemExit(f"could not read the tables in {map_path}")
    return tables[0], tables[1]


def is_date_pattern(literal: str) -> bool:
    return bool(PATTERN.match(literal) and STRONG.search(literal))


def source_lines(root: Path):
    """Yield (relative path, line number, line) for code lines of .ts/.tsx files outside SKIP_DIRS."""
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            if not name.endswith((".ts", ".tsx")):
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if rel.startswith(SKIP_DIRS):
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith(("//", "*", "/*", "import ")):
                    continue
                yield rel, lineno, line


def find_patterns(root: Path):
    """Yield (pattern, 'file:line') for every date-pattern literal under root."""
    for rel, lineno, line in source_lines(root):
        for match in LITERAL.finditer(line):
            literal = next(g for g in match.groups() if g is not None)
            if is_date_pattern(literal):
                yield literal, f"{rel}:{lineno}"


def unknown_patterns(root: Path) -> dict[str, list[str]]:
    fns, dj = load_tables(root / MAP_FILE)
    known = set(fns) | set(fns.values()) | set(dj) | set(dj.values())
    known |= set(ALLOWED) | set(NOT_DATES)
    found: dict[str, list[str]] = {}
    for literal, where in find_patterns(root):
        if literal in known:
            continue
        if where.rsplit(":", 1)[0] in PATH_ALLOWED.get(literal, ()):
            continue
        found.setdefault(literal, []).append(where)
    return found


def locale_calls(root: Path) -> dict[str, list[int]]:
    """Every toLocale*String / Intl.DateTimeFormat / formatRelative call: file -> line numbers."""
    found: dict[str, list[int]] = {}
    for rel, lineno, line in source_lines(root):
        if LOCALE_CALL.search(line):
            found.setdefault(rel, []).append(lineno)
    return found


def unreviewed_locale_calls(
    root: Path, allowed: dict[str, str] | None = None
) -> tuple[dict[str, list[int]], list[str]]:
    """(calls in files not on the allow-list, allow-list files with no such call any more)."""
    allowed = LOCALE_CALLS_ALLOWED if allowed is None else allowed
    calls = locale_calls(root)
    unreviewed = {f: lines for f, lines in calls.items() if f not in allowed}
    stale = sorted(f for f in allowed if f not in calls)
    return unreviewed, stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("files", nargs="*", help="ignored: the whole tree is always checked")
    args = parser.parse_args(argv)
    found = unknown_patterns(args.root)
    unreviewed, stale = unreviewed_locale_calls(args.root)
    if args.root.resolve() != DEFAULT_ROOT.resolve():
        stale = []  # the allow-list names files of the real tree only
    if found:
        print("Date display patterns not in the India date tables (P1-T7b):")
        for literal, places in sorted(found.items()):
            print(f"  {literal!r}: {', '.join(places)}")
        print(
            "Add each to apps/frontend/src/Utils/ihr/indiaDateFormats.ts (display) "
            "or to ALLOWED / PATH_ALLOWED in scripts/check-date-formats.py "
            "(API/input/no year), with a reason."
        )
    if unreviewed:
        print("Dates formatted by browser language or en-US locale (P1-T7b):")
        for rel, lines in sorted(unreviewed.items()):
            print(f"  {rel}: lines {', '.join(map(str, lines))}")
        print(
            "Show full dates/times with indiaDateTime etc. from Utils/ihr/indiaDateFormats.ts, "
            "or add the file to LOCALE_CALLS_ALLOWED with a reviewed reason."
        )
    if stale:
        print("LOCALE_CALLS_ALLOWED entries with no such call any more (remove them):")
        for rel in stale:
            print(f"  {rel}")
    return 1 if found or unreviewed or stale else 0


if __name__ == "__main__":
    sys.exit(main())
