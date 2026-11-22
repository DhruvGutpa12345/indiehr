# P1-T24d build note: reason box and decision buttons stay in view

Owner click-through (2026-11-22): on the reference list updates item sheet the Reason box and the
Accept / Keep ours buttons sat at the very bottom of a scrolling sheet; at 1280x800 they were not found.

## Files
- `apps/frontend/src/components/ihr/IhrPackUpdates.tsx` (ours; no upstream CARE file touched, so nothing
  for docs/UPSTREAM_PATCHES.md).
- `apps/frontend/tests-ihr/pack-updates.spec.ts` (Playwright).

## The change (layout only)
- `PackSheet`: `SheetContent` is now `flex flex-col gap-0 overflow-y-auto`: the header stays and the body fills
  the rest; the sheet itself scrolls only when the pinned layout cannot fit (review D1).
- `SheetBody` (loaded state): the outer `packs-detail` is a flex column. The facts, "why" note and field
  differences sit in a new `packs-detail-scroll` region (`min-h-24 flex-1 overflow-y-auto`, `role=region`, `tabIndex=0`, `aria-label` = existing `ihr_packs_sheet_title`). The reason
  box, its hint and error, the answer line and the buttons sit in a new `packs-footer` below it
  (`border-t bg-white pt-4`), always visible. Same for Waiting (Accept + Keep ours) and Kept as ours
  (Accept + the "kept" note): one shared footer.
- No change to wording, keys, API calls, the reason rule or any handler. Loading and error states untouched.

## Test (written first)
`pack-updates.spec.ts` gets `expectDecisionsInView`: with the sheet open, at 1280x800 and 360x740, after
scrolling every part of the sheet to its top, it requires `packs-footer` visible and `packs-reason` plus the
buttons (`packs-accept` and `packs-keep` for a waiting item; `packs-accept` and `packs-kept` for a kept
item) to lie fully inside the viewport. It runs in the existing "opening an item" and "kept tab" steps.
It also checks a short 640x300 window, where the buttons must be reachable by scrolling. It uses the retrying
`toBeInViewport({ratio: 1})`, so the sheet's slide-in cannot fail it. By reasoning only the old layout fails it
(no `packs-footer`, buttons below the fold); it was NOT seen red on the old layout, nor run at all here
(no stack or browser on this machine). It relies on CI.

## Ran locally
- `prettier --check` on both files: clean. `tsc --noEmit` (heap 1800): no output (about 1.9 GB free). `eslint` on the component: no errors (49 pre-existing warnings
  about `ihr_*` keys missing from upstream en.json, which `deploy/locale/en.json` supplies at build).
- `python3 scripts/check-ihr-frontend.py`: "ihr web-app anchors: ok".
- `pre-commit run --all-files`: see run output in the report.

## Left to CI / the owner
- `tsc`, vitest and Playwright (only ~1.2 GB free here; the repo rule is never to build the frontend locally).
- Owner's look: open /facility/<id>/ihr/packs, open an item at 1280x800 and at phone width: reason box and
  both buttons stay at the bottom while the differences scroll above.

## Review
| id | finding | severity (reviewer -> challenger) | fix | test |
|----|---------|-----------------------------------|-----|------|
| D1 | `overflow-hidden` on the sheet clips the footer when header + footer exceed the height (landscape phone, big zoom, tall error, dragged textarea) | medium -> medium (confirmed) | `overflow-y-auto` on SheetContent; `min-h-24` on the scroll region; body wrapper no longer `min-h-0` so it grows to its content | spec: 640x300 window, each control scrolled to and `toBeInViewport` |
| D2 | one-shot `boundingBox()` maths can fail during the 500 ms slide-in | medium (flake) -> confirmed | `expect(...).toBeInViewport({ ratio: 1 })` (retries) | spec `expectDecisionsInView` |
| D3 | the check was not seen failing on the old layout | low | none in code; stated above | CI |
| D4 | scroll region not keyboard-reachable or named; redundant `bg-white` | low -> partly | `tabIndex=0`, `role=region`, `aria-label` from `ihr_packs_sheet_title`; `bg-white` dropped | none (markup) |

## CI on d920bbcbf (2026-11-22)

CI 45011212665 green; Apps CI 45011212551: frontend build green, Playwright 33 passed and **1 failed**: `expectDecisionsInView` found `packs-reason` only 39 % in the 1280×800 window. Cause: the D1 fix dropped `min-h-0` from the `packs-detail` column, so that flex item could not shrink below its content and the whole sheet scrolled instead (a flex item's default minimum height is its content). Fix: `min-h-0` back on `packs-detail`, keeping `overflow-y-auto` on the sheet and `min-h-24` on the scroll region; the check that caught it is the D2 retrying check, so it was seen red. CI on the fix: see the next commit.
