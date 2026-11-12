#!/usr/bin/env bash
# Released reference pack versions are never changed (P1-T15a). Hospitals record the sha256 of
# what they applied, so a rebuilt "v1" would be refused at start-up for the whole fleet (or,
# worse, quietly taken by a fresh database). A new version is built with scripts/pack-build.sh
# and added instead.
#
#   scripts/check-pack-versions.sh               pre-commit: a staged change, deletion, rename
#                                                or type change of a version file is refused
#                                                (added files pass)
#   scripts/check-pack-versions.sh --history     CI: --base against every commit that changed
#                                                MANIFEST.json
#   scripts/check-pack-versions.sh --base <ref>  every release listed in MANIFEST.json at
#                                                <ref> is still listed unchanged at HEAD (same
#                                                pack, version, file and sha256), and its file
#                                                at HEAD still hashes to that sha256
set -euo pipefail
PACKS=plugins/ihr_platform/ihr_refdata/packs

if [[ "${1:-}" == "--history" ]]; then
  # CI: against every manifest ever committed, so a deletion pushed on its own and a rebuilt
  # version pushed later are still refused (review C3/S3 follow-up)
  SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
  ROOT="$(git rev-parse --show-toplevel)"
  cd "$ROOT"
  n=0
  while read -r commit; do
    [[ -n "$commit" ]] || continue
    "$SELF" --base "$commit" >/dev/null
    n=$((n + 1))
  done < <(git log --format=%H -- "$PACKS/MANIFEST.json")
  echo "check-pack-versions: every release in $n committed manifest(s) is unchanged"
  exit 0
fi

if [[ "${1:-}" == "--base" ]]; then
  BASE="${2:?--base needs a git ref}"
  ROOT="$(git rev-parse --show-toplevel)"
  cd "$ROOT"
  if ! git cat-file -e "$BASE^{commit}" 2>/dev/null; then
    echo "check-pack-versions: $BASE is not a commit here (fetch the full history)" >&2
    exit 2
  fi
  exec python3 - "$BASE" "$PACKS" <<'PY'
import hashlib
import json
import subprocess
import sys

base, packs = sys.argv[1], sys.argv[2]


def show(rev, path):
    run = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, check=False)
    return run.stdout if run.returncode == 0 else None


def releases(rev):
    data = show(rev, f"{packs}/MANIFEST.json")
    if data is None:
        return None
    return {(e["pack"], e["version"]): e for e in json.loads(data)["releases"]}


old = releases(base)
if old is None:
    print(f"check-pack-versions: no MANIFEST.json at {base}: nothing released before")
    sys.exit(0)
new = releases("HEAD") or {}
problems = []
for key, entry in sorted(old.items()):
    name = f"{entry['pack']} v{entry['version']}"
    now = new.get(key)
    if now is None:
        problems.append(f"{name} was released and is no longer listed")
        continue
    if (now["file"], now["sha256"]) != (entry["file"], entry["sha256"]):
        problems.append(f"{name} was released as {entry['file']} {entry['sha256'][:12]}, "
                        f"now listed as {now['file']} {now['sha256'][:12]}")
        continue
    data = show("HEAD", f"{packs}/{entry['file']}")
    if data is None:
        problems.append(f"{name}: {entry['file']} is gone")
    elif hashlib.sha256(data).hexdigest() != entry["sha256"]:
        problems.append(f"{name}: {entry['file']} no longer hashes to its released sha256")
if problems:
    print("Released reference pack versions are never changed; build a new version instead "
          "(scripts/pack-build.sh <pack>):", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    sys.exit(1)
print(f"check-pack-versions: {len(old)} release(s) from {base} unchanged")
PY
fi

changed="$(git diff --cached --name-status --diff-filter=MDRT -- "$PACKS/*/[0-9]*.json")"
if [[ -n "$changed" ]]; then
  echo "Released reference pack versions are never changed, deleted or renamed; build a new" >&2
  echo "version instead (scripts/pack-build.sh <pack>):" >&2
  echo "$changed" >&2
  exit 1
fi
