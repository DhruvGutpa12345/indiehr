#!/usr/bin/env bash
# Sync CARE upstream into apps/ via squashed git subtree (ADR-006).
#
# Usage: scripts/sync-upstream.sh <backend|frontend|all> [ref] [--dry-run]
#   ref        upstream commit/branch to sync to (default: the branch in upstream.lock)
#   --dry-run  report what would change; touch nothing
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
LOCK="upstream.lock"
PATCHES="docs/UPSTREAM_PATCHES.md"

usage() { sed -n '4,7p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

TARGET="${1:-}"; [[ -z "$TARGET" ]] && usage; shift
REF=""; DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -*) usage ;;
    *) REF="$arg" ;;
  esac
done
case "$TARGET" in
  backend|frontend) APPS=("$TARGET") ;;
  all) APPS=(backend frontend); [[ -n "$REF" ]] && { echo "A ref can only be given for a single app." >&2; exit 1; } ;;
  *) usage ;;
esac

lock_get() { grep -E "^$1=" "$LOCK" | cut -d= -f2-; }
lock_set() { sed -i "s|^$1=.*|$1=$2|" "$LOCK"; }

if [[ $DRY_RUN -eq 0 && -n "$(git status --porcelain)" ]]; then
  echo "Working tree is not clean; commit or stash first." >&2
  exit 1
fi

for app in "${APPS[@]}"; do
  prefix="$(lock_get "$app.prefix")"
  repo="$(lock_get "$app.repo")"
  branch="$(lock_get "$app.branch")"
  pinned="$(lock_get "$app.sha")"
  remote="upstream-$app"

  git remote get-url "$remote" >/dev/null 2>&1 || git remote add "$remote" "$repo"
  git fetch -q --no-tags "$remote" "$branch"
  [[ -n "$REF" ]] && git fetch -q --no-tags "$remote" "$REF"
  new="$(git rev-parse "${REF:-$remote/$branch}^{commit}")"

  echo "== $app ($prefix)"
  echo "   pinned: ${pinned:0:12}   target: ${new:0:12}"
  if [[ "$new" == "$pinned" ]]; then
    echo "   already up to date"
    continue
  fi

  count="$(git rev-list --count "$pinned..$new")"
  echo "   upstream commits since pin: $count"
  git diff --shortstat "$pinned" "$new" | sed 's/^/   /'

  # Files we patched (listed in UPSTREAM_PATCHES.md) that upstream also changed -> likely conflicts.
  changed="$(git diff --name-only "$pinned" "$new")"
  risky=0
  while IFS= read -r f; do
    rel="${f#"$prefix"/}"
    if [[ "$rel" != "$f" ]] && grep -qxF "$rel" <<<"$changed"; then
      echo "   ! upstream also changed our patched file: $f"
      risky=1
    fi
  done < <(grep -oE "\`$prefix/[^\`]+\`" "$PATCHES" 2>/dev/null | tr -d '`' || true)
  [[ $risky -eq 0 ]] && echo "   no overlap with our patched files"

  if [[ $DRY_RUN -eq 1 ]]; then
    continue
  fi

  git subtree pull -q --prefix="$prefix" --squash "$remote" "$new" \
    -m "Sync $app with upstream ${new:0:12} ($count commits)"
  lock_set "$app.sha" "$new"
  lock_set "$app.date" "$(git show -s --format=%cs "$new")"
  git add "$LOCK"
  git commit -q -m "Update upstream.lock: $app -> ${new:0:12}"
  echo "   synced; remember to update docs/UPSTREAM.md and run the full test suite"
done

[[ $DRY_RUN -eq 1 ]] && echo "(dry run: nothing changed)"
exit 0
