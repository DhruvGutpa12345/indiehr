#!/usr/bin/env python3
"""Merge our translations (deploy/locale/<lang>.json) into the web app's (P1-T7c).

Our strings live outside apps/frontend so upstream syncs never conflict. At build time they
fill only the strings upstream has not translated. Upstream strings are replaced only by
owner-approved corrections in deploy/locale/<lang>.corrections.json.

    scripts/merge-locale.py --check                  # validate only (pre-commit, CI)
    scripts/merge-locale.py                          # write apps/frontend/public/locale/<lang>.json
    scripts/merge-locale.py --out-dir DIR            # write merged files to DIR instead

Checks: every key exists in English; {{placeholders}}, $t(...) references and <tags> match
the English exactly; no empty strings; plural pairs complete (_one with _other).
Stdlib only.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OURS = ROOT / "deploy" / "locale"
UPSTREAM = ROOT / "apps" / "frontend" / "public" / "locale"
TOKEN_RE = re.compile(r"\{\{[^{}]*\}\}|\$t\([^)]*\)|</?[A-Za-z0-9][\w-]*\s*/?>")
PLURAL_RE = re.compile(r"^(.*)_(zero|one|two|few|many|other)$")


def tokens(text):
    return Counter(re.sub(r"\s+", "", t) for t in TOKEN_RE.findall(text))


def _leaves(value, path=""):
    """(dotted path, string) for a value that may be a nested object, as i18next allows."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from _leaves(inner, f"{path}.{key}" if path else key)
    else:
        yield path, value


def check(lang, ours, english, corrections, upstream):
    problems = []
    for name, table in (("", ours), (".corrections", corrections)):
        for key, value in table.items():
            where = f"{lang}{name}.json: {key}"
            if key not in english:
                problems.append(f"{where}: not an English key")
                continue
            source = dict(_leaves(english[key]))
            mine = dict(_leaves(value))
            if set(source) != set(mine):
                problems.append(f"{where}: shape differs from English")
                continue
            for path, text in mine.items():
                label = f"{where}{'.' + path if path else ''}"
                if not isinstance(text, str) or not text.strip():
                    problems.append(f"{label}: empty")
                elif tokens(text) != tokens(source[path]):
                    problems.append(
                        f"{label}: placeholders/tags differ from English "
                        f"{sorted(tokens(source[path]).elements())} vs "
                        f"{sorted(tokens(text).elements())}"
                    )
        if name == ".corrections":
            problems += [
                f"{lang}.corrections.json: {key}: upstream has no string to correct"
                for key in table
                if key not in upstream
            ]
    merged = merge(ours, corrections, upstream)
    for key in merged:
        if (m := PLURAL_RE.match(key)) and m.group(2) == "one":
            other = f"{m.group(1)}_other"
            if other in english and other not in merged:
                problems.append(f"{lang}: {key} has no {other}")
    return problems


def merge(ours, corrections, upstream):
    merged = dict(upstream)
    for key, value in ours.items():
        merged.setdefault(key, value)  # fill gaps only
    merged.update(corrections)
    return merged


def _load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="validate, write nothing")
    parser.add_argument("--out-dir", type=Path, help="write merged files here")
    args = parser.parse_args()

    english = _load(UPSTREAM / "en.json")
    failed = False
    for path in sorted(OURS.glob("*.json")):
        if path.name.endswith(".corrections.json"):
            continue
        lang = path.stem
        ours = _load(path)
        corrections = _load(OURS / f"{lang}.corrections.json")
        upstream = _load(UPSTREAM / path.name)
        problems = check(lang, ours, english, corrections, upstream)
        for problem in problems:
            print(problem, file=sys.stderr)
        failed |= bool(problems)
        filled = sum(1 for key in ours if key not in upstream)
        print(
            f"{lang}: {len(upstream)} upstream + {filled} ours + {len(corrections)} "
            f"corrections = {len(merge(ours, corrections, upstream))} of {len(english)}"
        )
        if not args.check and not problems:
            out = (args.out_dir or UPSTREAM) / path.name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(
                    merge(ours, corrections, upstream), ensure_ascii=False, indent=2
                )
                + "\n",
                encoding="utf-8",
            )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
