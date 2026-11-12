#!/usr/bin/env python3
"""Mask the dev stack's generated secrets in GitHub Actions logs (P1-T1d).

Prints one `::add-mask::` workflow command per secret value from deploy/dev/*.env (keys named
*PASSWORD*, *SECRET*, *_KEY) and from deploy/seed/TEST_USERS.md (hospital passwords, staff
passwords, authenticator keys in both forms). The CI steps that write new ones run it from an
EXIT trap, so the masks are registered even when the step fails. The runner hides these command
lines, so no value appears in the log. Never run it locally (it would print the values).

It fails closed (exit 1, naming keys and lines, never values) when the inputs no longer look
the way it expects, so a generator change breaks the step instead of masking less:
- an env key that is neither secret-named nor on the reviewed non-secret list below;
- a secret-named env value too short to mask;
- in TEST_USERS.md, a hospital section without its password line, a user-table row whose
  Password or Authenticator key cell yields nothing, a `raw:` comment that yields nothing, or
  no value at all.
Tests: python3 -m unittest scripts/test_ci_mask_dev_secrets.py
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SECRET_KEY = re.compile(r"PASSWORD|SECRET|_KEY$")
# Reviewed: env keys that hold no secret (names, client ids, addresses, bucket names)
NON_SECRET_SUFFIXES = ("_USERNAME", "_CLIENT_ID", "_ENDPOINT", "_BUCKET")
MIN_LENGTH = 6  # shorter values would mask ordinary words in the log

BACKTICKED = re.compile(r"`([^`\n]+)`")
# "**Password for every user below:** `…`" and the Keycloak admin's "password `…`"
PASSWORD_LINE = re.compile(r"[Pp]assword[^`\n]*`([^`\n]+)`")
RAW = re.compile(r"raw:([^\s<>`]*)")
HOSPITAL_HEADING = re.compile(r"^## .+ — https?://")


def env_values(dev_dir: Path) -> tuple[set[str], list[str]]:
    values, problems = set(), []
    for env in sorted(dev_dir.glob("*.env")):
        for number, line in enumerate(env.read_text().splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key, sep, value = stripped.partition("=")
            key, value = key.strip(), value.strip().strip("\"'")
            where = f"{env.name}:{number} ({key or 'no key'})"
            if not sep:
                problems.append(f"{where}: not KEY=value")
            elif SECRET_KEY.search(key):
                if len(value) < MIN_LENGTH:
                    problems.append(f"{where}: secret value too short to mask")
                else:
                    values.add(value)
            elif not key.endswith(NON_SECRET_SUFFIXES):
                if len(value) >= MIN_LENGTH:
                    values.add(value)  # masked anyway, and the step still fails
                problems.append(
                    f"{where}: neither secret-named nor on the reviewed non-secret list"
                )
    return values, problems


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def test_user_values(text: str) -> tuple[set[str], list[str]]:
    values, problems = set(), []
    lines = text.splitlines()

    for number, line in enumerate(lines, 1):
        for match in RAW.finditer(line):
            if len(match.group(1)) < MIN_LENGTH:
                problems.append(f"TEST_USERS.md:{number}: a raw: key that yields nothing")
            else:
                values.add(match.group(1))
        values.update(PASSWORD_LINE.findall(line))

    # Every hospital section names its password on a "**Password …** `…`" line
    for number, line in enumerate(lines, 1):
        if not HOSPITAL_HEADING.match(line):
            continue
        section = []
        for following in lines[number:]:
            if following.startswith("## ") or following.startswith("<!--"):
                break
            section.append(following)
        if not any(PASSWORD_LINE.search(item) for item in section):
            problems.append(f"TEST_USERS.md:{number}: hospital section without a password")

    # User tables: every row's Password cell, and its Authenticator key cell unless "none …"
    header: list[str] | None = None
    for number, line in enumerate(lines, 1):
        if not line.startswith("|"):
            header = None
            continue
        cells = _cells(line)
        if header is None:
            header = cells
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue  # the |---| row
        for column in ("Password", "Authenticator key"):
            if column not in header:
                continue
            index = header.index(column)
            cell = cells[index] if index < len(cells) else ""
            found = [v for v in BACKTICKED.findall(cell) if len(v) >= MIN_LENGTH]
            if found:
                values.update(found)
            elif not (column == "Authenticator key" and cell.lower().startswith("none")):
                problems.append(f"TEST_USERS.md:{number}: {column} cell yields nothing")

    if not values:
        problems.append("TEST_USERS.md: no value found at all")
    return values, problems


def collect(root: Path | None = None) -> tuple[list[str], list[str]]:
    """(values to mask, problems): problems name files, lines and keys, never values."""
    root = root or ROOT
    values, problems = env_values(root / "deploy" / "dev")
    users = root / "deploy" / "seed" / "TEST_USERS.md"
    if users.exists():
        more, more_problems = test_user_values(users.read_text())
        values |= more
        problems += more_problems
    return sorted(values), problems


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        sys.exit("Only for GitHub Actions: it would print the secrets here.")
    values, problems = collect()
    # Mask everything that was found, then fail: the step stops before anything is dumped.
    for value in values:
        print(f"::add-mask::{value}")
    if problems:
        print(
            "ci-mask-dev-secrets: the inputs changed shape; review the script:\n"
            + "\n".join(problems),
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
