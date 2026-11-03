#!/usr/bin/env bash
# Sync CARE upstream into apps/ via squashed git subtree (ADR-006).
#
# Usage: scripts/sync-upstream.sh <backend|frontend|all> [sha] [--dry-run] [--task ID]
#   sha        full 40-character upstream commit to sync to (default: tip of the branch in upstream.lock)
#   --dry-run  report what would change; touch nothing
#   --task ID  task ID for the commit messages (required unless --dry-run), e.g. --task P2-T4
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
LOCK="upstream.lock"
PATCHES="docs/UPSTREAM_PATCHES.md"

usage() { sed -n '4,8p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

TARGET="${1:-}"; [[ -z "$TARGET" ]] && usage; shift
REF=""; DRY_RUN=0; TASK=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --task) TASK="${2:-}"; shift ;;
    -*) usage ;;
    *) REF="$1" ;;
  esac
  shift
done
if [[ -n "$REF" && ! "$REF" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Give the full 40-character commit SHA (short SHAs cannot be fetched)." >&2; exit 1
fi
if [[ $DRY_RUN -eq 0 && ! "$TASK" =~ ^P[0-9]+-T[0-9]+[a-z]?$ ]]; then
  echo "A real sync needs --task <ID> (e.g. --task P2-T4) for the commit messages." >&2; exit 1
fi
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
  if ! git merge-base --is-ancestor "$pinned" "$new"; then
    echo "   ! WARNING: the pinned commit is not an ancestor of the target (upstream history was rewritten?)."
    echo "     Review carefully; the merge may bring unexpected changes."
  fi
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

  if ! git subtree pull -q --prefix="$prefix" --squash "$remote" "$new" \
    -m "$TASK: sync $app with upstream ${new:0:12} ($count commits)"; then
    echo
    echo "MERGE CONFLICT while syncing $app. upstream.lock was NOT updated."
    echo "Follow the conflict runbook in docs/UPSTREAM.md, then commit, or abort with: git merge --abort"
    exit 2
  fi
  lock_set "$app.sha" "$new"
  lock_set "$app.date" "$(git show -s --format=%cs "$new")"
  git add "$LOCK"
  git commit -q -m "$TASK: update upstream.lock ($app -> ${new:0:12})"
  echo "   synced. Next: re-check our lock-file security bumps (docs/UPSTREAM.md), update the table there,"
  echo "   then push and confirm Apps CI is green (full backend suite + frontend build + Trivy)."
done

[[ $DRY_RUN -eq 1 ]] && echo "(dry run: nothing changed)"
exit 0
