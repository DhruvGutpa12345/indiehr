#!/usr/bin/env python3
"""Every browser storage key in the web app is classified (P1-T1d, Q-T9).

The browser sign-out spec (apps/frontend/tests-ihr) only sees keys written on the pages it
visits. This check reads apps/frontend/src instead and finds every key passed to
localStorage/sessionStorage getItem, setItem or removeItem, to jotai's atomWithStorage, and to
the key helpers listed in tests-ihr/storage-keys.json (e.g. useView). Each key must be:
- allowed to remain after sign-out ("may_remain" or "content_checked" in storage-keys.json), or
- cleared by clearStaffStorage (Utils/ihr/staffSession.ts): its LEGACY_KEYS,
  PERSISTED_QUERIES_KEY, PATIENT_PORTAL_KEYS or CACHED_PREFIXES, a helper it calls
  ("cleared_by_helpers"), or any sessionStorage key while it calls sessionStorage.clear().
A key whose name cannot be worked out fails too, unless its file is a reviewed dynamic site.

  python3 scripts/check-storage-keys.py      (exit 1 on an unclassified key)
Tests: python3 -m unittest scripts/test_check_storage_keys.py
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = Path("apps") / "frontend"
SRC = FRONTEND / "src"
CLASSIFIED = FRONTEND / "tests-ihr" / "storage-keys.json"
STAFF_SESSION = Path("Utils") / "ihr" / "staffSession.ts"
CONSTANTS = Path("common") / "constants.tsx"

ARG = r"""("[^"\n]*"|'[^'\n]*'|`[^`]*`|[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)"""
STORAGE_CALL = re.compile(
    # storage()? is staffSession.ts's own guarded accessor for localStorage
    r"\b(localStorage|sessionStorage|storage\(\)\??)\s*\.\s*(?:setItem|getItem|removeItem)\s*\(\s*"
    + ARG
)
ATOM = re.compile(r"\batomWithStorage\s*(?:<.*?>)?\s*\(\s*" + ARG, re.S)
CONST = re.compile(r"""^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*["']([^"'\n]+)["']""", re.M)


class Key:
    def __init__(self, store: str, name: str, prefix: bool, where: str):
        self.store, self.name, self.prefix, self.where = store, name, prefix, where

    def __repr__(self):
        return f"{self.store} {self.name}{'*' if self.prefix else ''} ({self.where})"


def _literal(arg: str):
    """(name, is_prefix) for a string literal, None for anything else."""
    if arg[0] in "\"'":
        return arg[1:-1], False
    if arg[0] == "`":
        body = arg[1:-1]
        return (body.split("${")[0], True) if "${" in body else (body, False)
    return None


def _call_text(text: str, start: int) -> str:
    """The text of a call's arguments, from just after its "(" to the matching ")"."""
    depth = 1
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[start:index]
    return text[start:]


def _object_strings(text: str, name: str) -> dict[str, str]:
    """String members of `export const name = { member: "value", … }`."""
    match = re.search(rf"const\s+{name}\s*=\s*\{{(.*?)\}}", text, re.S)
    if not match:
        return {}
    return dict(re.findall(r"""(\w+)\s*:\s*["']([^"']+)["']""", match.group(1)))


def _array_strings(text: str, name: str, members: dict[str, str]) -> list[str]:
    match = re.search(rf"const\s+{name}\s*=\s*\[(.*?)\]", text, re.S)
    if not match:
        raise ValueError(f"{STAFF_SESSION}: no array {name}")
    values = re.findall(r"""["']([^"']+)["']""", match.group(1))
    for member in re.findall(r"LocalStorageKeys\.(\w+)", match.group(1)):
        values.append(members[member])
    return values


def _function_body(text: str, name: str) -> str:
    match = re.search(rf"function\s+{name}\s*\(\)\s*\{{", text)
    if not match:
        raise ValueError(f"{STAFF_SESSION}: no function {name}")
    depth, start = 1, match.end()
    for index in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[start:index]
    return text[start:]


def find_keys(src: Path, classified: dict) -> tuple[list[Key], list[str]]:
    files = sorted(
        path for path in src.rglob("*") if path.suffix in (".ts", ".tsx") and path.is_file()
    )
    texts = {path.relative_to(src).as_posix(): path.read_text(encoding="utf-8") for path in files}
    members = _object_strings(texts.get(CONSTANTS.as_posix(), ""), "LocalStorageKeys")
    globals_: dict[str, str] = {}
    for text in texts.values():
        for name, value in CONST.findall(text):
            globals_.setdefault(name, value)
    dynamic = classified.get("dynamic_sites", {})
    helpers = classified.get("key_helpers", {})
    helper_call = (
        re.compile(r"\b(" + "|".join(map(re.escape, helpers)) + r")\s*\(\s*" + ARG)
        if helpers
        else None
    )

    keys: list[Key] = []
    problems: list[str] = []
    for rel, text in texts.items():
        local_consts = dict(CONST.findall(text))

        def resolve(arg: str, store: str, where: str):
            literal = _literal(arg)
            if literal:
                keys.append(Key(store, literal[0], literal[1], where))
                return
            if arg.startswith("LocalStorageKeys.") and arg.split(".")[1] in members:
                keys.append(Key(store, members[arg.split(".")[1]], False, where))
                return
            value = local_consts.get(arg) or globals_.get(arg)
            if value is not None:
                keys.append(Key(store, value, False, where))
            elif rel not in dynamic:
                problems.append(f"{where}: storage key `{arg}` cannot be worked out")

        for match in STORAGE_CALL.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            store = "session" if match.group(1) == "sessionStorage" else "local"
            resolve(match.group(2), store, f"src/{rel}:{line}")
        for match in ATOM.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            args = _call_text(text, match.start(1))
            store = "session" if "sessionStorage" in args else "local"
            resolve(match.group(1), store, f"src/{rel}:{line}")
        if helper_call and rel not in dynamic:
            for match in helper_call.finditer(text):
                if re.search(rf"function\s+{match.group(1)}\s*\($", text[: match.end(1) + 1]):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                resolve(match.group(2), helpers[match.group(1)], f"src/{rel}:{line}")
    for rel, names in dynamic.items():
        if rel not in texts:
            problems.append(f"storage-keys.json: dynamic site src/{rel} no longer exists")
        for name in names:
            keys.append(Key("local", name, name.endswith("--"), f"src/{rel} (dynamic site)"))
    return keys, problems


def cleared_by_staff_sign_out(src: Path, classified: dict) -> tuple[set[str], list[str], bool]:
    """(exact keys, prefixes, whether every sessionStorage key is cleared)."""
    text = (src / STAFF_SESSION).read_text(encoding="utf-8")
    members = _object_strings((src / CONSTANTS).read_text(encoding="utf-8"), "LocalStorageKeys")
    body = _function_body(text, "clearStaffStorage")
    exact: set[str] = set()
    for array in ("LEGACY_KEYS", "PATIENT_PORTAL_KEYS"):
        if array in body or re.search(rf"\.\.\.{array}\b", body):
            exact.update(_array_strings(text, array, members))
    persisted = re.search(r"""const\s+PERSISTED_QUERIES_KEY\s*=\s*["']([^"']+)["']""", text)
    if persisted and "PERSISTED_QUERIES_KEY" in body:
        exact.add(persisted.group(1))
    prefixes = _array_strings(text, "CACHED_PREFIXES", members) if "CACHED_PREFIXES" in body else []
    for prefix, helper in classified.get("cleared_by_helpers", {}).items():
        if helper.split(".")[-1] in body and helper.split(".")[0] in body:
            prefixes.append(prefix)
    return exact, prefixes, "sessionStorage.clear()" in body


def check(root: Path = ROOT) -> list[str]:
    src = root / SRC
    classified = json.loads((root / CLASSIFIED).read_text(encoding="utf-8"))
    keys, problems = find_keys(src, classified)
    exact, prefixes, session_cleared = cleared_by_staff_sign_out(src, classified)
    may_remain = classified["may_remain"]
    allowed_exact = set(may_remain["exact"]) | set(classified["content_checked"]["local"])
    allowed_exact |= set(classified["content_checked"]["session"])
    allowed_prefixes = list(may_remain["prefixes"]) + prefixes

    for key in keys:
        if key.store == "session" and session_cleared:
            continue
        if not key.prefix and (key.name in allowed_exact or key.name in exact):
            continue
        if any(key.name.startswith(prefix) for prefix in allowed_prefixes) and key.name:
            continue
        problems.append(
            f"{key.where}: {key.store}Storage key `{key.name}{'…' if key.prefix else ''}` is not "
            "classified: add it to tests-ihr/storage-keys.json (may_remain, with a reason) or "
            "clear it in clearStaffStorage"
        )
    return sorted(set(problems))


def main():
    problems = check()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        sys.exit(1)
    print("check-storage-keys: every browser storage key is classified")


if __name__ == "__main__":
    main()
