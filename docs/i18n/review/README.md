# Hindi review sheets

Each batch of Hindi strings comes with a sheet for your review. Open it in any spreadsheet
(LibreOffice: choose UTF-8 and "comma" when it asks).

| Column | What it is |
|---|---|
| key | The string's name in the app (do not change) |
| english | What the app shows in English |
| hindi | Our draft |
| clinical | **yes** = a clinician acts on it. Please read every one of these |
| note | Why a word was chosen, or where the meaning was unclear |
| used_in | The screen's source file (helps when a short word is ambiguous) |
| ok (your mark) | Put **ok**, or leave empty if you did not check it |
| your correction | Your Hindi, if ours is wrong |

Clinical rows are at the top. Send the sheet back (or tell me the keys and your wording); I
apply your corrections, mark the batch reviewed in TRACKER.md and fix the glossary
(`docs/i18n/hi-glossary.md`) if a term changes, so later batches follow it.

**Upstream corrections** (`hi-upstream-corrections.csv`): strings CARE already translated
that look wrong. The app keeps CARE's wording until you approve a row; approved rows go to
`deploy/locale/hi.corrections.json` and then win over CARE's.

| Batch | Sheet | Strings | Clinical | Reviewed |
|---|---|---|---|---|
| 1 (P1-T7c) | `hi-batch-1.csv` | 1,502 | 165 | waiting for you |
| Upstream corrections (P1-T7c) | `hi-upstream-corrections.csv` | 211 (13 unsafe, 72 wrong meaning) | 70 | waiting for you; **unsafe rows first** |
