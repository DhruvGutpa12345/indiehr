#!/usr/bin/env python3
"""Report pinned dependencies that are behind their latest stable release (P0-T13).

Backend: every package in apps/backend/Pipfile.lock (default + develop).
Frontend: direct dependencies in apps/frontend/package.json, at the version in package-lock.json.
Report only: always exits 0. Writes Markdown to $GITHUB_STEP_SUMMARY when set.
"""

import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRERELEASE = re.compile(r"(a|b|rc|dev|alpha|beta|pre|next|canary|-)", re.IGNORECASE)


def fetch_json(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            return json.load(response)
    except Exception:  # network issues should not break a report-only job
        return None


def version_key(version):
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.+]", version))


def pypi_latest(name):
    data = fetch_json(f"https://pypi.org/pypi/{name}/json")
    if not data:
        return None
    stable = [v for v, files in data["releases"].items() if files and not PRERELEASE.search(v)]
    return max(stable, key=version_key) if stable else data["info"]["version"]


def npm_latest(name):
    data = fetch_json(f"https://registry.npmjs.org/{name.replace('/', '%2F')}/latest")
    return data.get("version") if data else None


def bump_kind(current, latest):
    cur, new = version_key(current), version_key(latest)
    if new <= cur:
        return None
    if new[:1] != cur[:1]:
        return "major"
    if new[:2] != cur[:2]:
        return "minor"
    return "patch"


def backend_pins():
    lock = json.loads((ROOT / "apps/backend/Pipfile.lock").read_text())
    pins = {}
    for section in ("default", "develop"):
        for name, info in lock.get(section, {}).items():
            if info.get("version", "").startswith("=="):
                pins[name] = info["version"][2:]
    return pins


def frontend_pins():
    package = json.loads((ROOT / "apps/frontend/package.json").read_text())
    lock = json.loads((ROOT / "apps/frontend/package-lock.json").read_text())["packages"]
    names = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    return {n: lock[f"node_modules/{n}"]["version"] for n in names if f"node_modules/{n}" in lock}


def report(title, pins, latest_fn):
    with ThreadPoolExecutor(max_workers=16) as pool:
        latest = dict(zip(pins, pool.map(latest_fn, pins)))
    rows = []
    for name, current in sorted(pins.items()):
        new = latest.get(name)
        kind = bump_kind(current, new) if new else None
        if kind:
            rows.append((kind, name, current, new))
    order = {"major": 0, "minor": 1, "patch": 2}
    rows.sort(key=lambda r: (order[r[0]], r[1]))
    counts = {k: sum(1 for r in rows if r[0] == k) for k in order}
    lines = [
        f"### {title}: {len(rows)} of {len(pins)} behind "
        f"(major {counts['major']}, minor {counts['minor']}, patch {counts['patch']})",
        "",
        "| Bump | Package | Pinned | Latest |",
        "|---|---|---|---|",
    ]
    lines += [f"| {k} | {n} | {c} | {l} |" for k, n, c, l in rows]
    return "\n".join(lines) + "\n"


def main():
    out = "## Outdated dependencies (report only)\n\n"
    out += report("Backend (Pipfile.lock)", backend_pins(), pypi_latest) + "\n"
    out += report("Frontend (direct deps)", frontend_pins(), npm_latest)
    print(out)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
