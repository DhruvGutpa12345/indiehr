#!/usr/bin/env bash
# Build a new version of a reference pack from CARE's own code (P1-T15a), and put it in place:
# plugins/ihr_platform/ihr_refdata/packs/<pack>/<version>.json plus the updated MANIFEST.json.
# The build runs in the care_local image with the plugins read-only and no network; only a
# fresh output folder is writable. CARE's commit is read from upstream.lock. A version file is
# never overwritten: commit the new one as it is (pre-commit refuses a changed one).
#
# Usage:
#   scripts/pack-build.sh care-valuesets                          # build if CARE's sets changed
#   scripts/pack-build.sh care-valuesets --allow-removal <slug>   # CARE dropped a set (reviewed)
#   scripts/pack-build.sh care-valuesets --check                  # exit 1 if a build is needed
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
PACKS="$ROOT/plugins/ihr_platform/ihr_refdata/packs"
PACK="${1:-}"
[[ "$PACK" =~ ^[a-z0-9-]+$ ]] || { sed -n '8,11p' "$0"; exit 1; }
shift
# CARE's commit always comes from upstream.lock and the output folder is ours (S9)
for arg in "$@"; do
  case "$arg" in
    --c|--c=*|--ca*|--o|--o=*|--ou*)  # with argparse's abbreviations (--care, --ou)
      echo "$arg is set by this script (upstream.lock, its own folder): leave it out" >&2
      exit 1 ;;
  esac
done
SHA="$(sed -n 's/^backend\.sha=//p' "$ROOT/upstream.lock")"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "No backend.sha in upstream.lock" >&2; exit 1; }

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
docker run --rm --network none \
  --env-file "$ROOT/apps/backend/docker/.local.env" \
  -e DATABASE_URL=postgres://postgres:postgres@db.invalid:5432/unused \
  -e DJANGO_SETTINGS_MODULE=ihr_platform.settings.local \
  -e PYTHONPATH=/ihr/plugins/ihr_platform \
  -e SNOWSTORM_DEPLOYMENT_URL=http://127.0.0.1:9/fhir \
  -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$ROOT/apps/backend:/app:ro" -v "$ROOT/plugins:/ihr/plugins:ro" -v "$OUT:/out" -w /app \
  --user "$(id -u):$(id -g)" care_local \
  python manage.py ihr_pack_build "$PACK" --care-commit "$SHA" --out /out "$@"

shopt -s nullglob
built=("$OUT/$PACK"/*.json)
[[ ${#built[@]} -gt 0 ]] || exit 0  # --check, or nothing changed
for file in "${built[@]}"; do
  target="$PACKS/$PACK/$(basename "$file")"
  if [[ -e "$target" ]]; then
    echo "$target exists: version files are never overwritten" >&2
    exit 1
  fi
done
for file in "${built[@]}"; do
  target="$PACKS/$PACK/$(basename "$file")"
  # no-clobber, then checked: a file that appeared since the check above is never replaced
  cp --no-clobber "$file" "$target" || true
  if ! cmp -s "$file" "$target"; then
    echo "$target appeared during the build: version files are never overwritten" >&2
    exit 1
  fi
  echo "added ${PACKS#"$ROOT"/}/$PACK/$(basename "$file")"
done
cp "$OUT/MANIFEST.json" "$PACKS/MANIFEST.json"
echo "updated ${PACKS#"$ROOT"/}/MANIFEST.json: commit both files"
