"""Search text and display comparison (P1-T10a1).

Search: lower case, Latin accents off, words of letters and digits in any script. "Diabetes
mellitus (disorder)" -> "diabetes mellitus disorder", so a search for "disorder" or "diab mell"
finds it, and CARE's medication picker's added " clinical drug" matches the "(clinical drug)" tag
of a SNOMED fully specified name. Marks that are part of Indian scripts (matras) are kept.

Display comparison is stricter: case and spacing are ignored, but every letter, digit and symbol
counts ("µg" is not "g", "°C" is not "C"), and two texts that are empty after folding are equal
only when they are the same text.
"""

import string
import unicodedata

_ASCII_PUNCT = str.maketrans(dict.fromkeys(string.punctuation, " "))


def _strip_latin_accents(text):
    out, base = [], ""
    for c in unicodedata.normalize("NFKD", text):
        if unicodedata.combining(c) and base and base < "ɐ":
            continue  # an accent on a Latin letter
        out.append(c)
        if not unicodedata.combining(c):
            base = c
    return unicodedata.normalize("NFC", "".join(out))


def normalise(text):
    text = _strip_latin_accents(str(text or "")).lower()
    # keep letters, digits and marks (vowel signs of Indian scripts are marks, not letters)
    kept = "".join(c if unicodedata.category(c)[0] in "LNM" else " " for c in text)
    return " ".join(kept.split())


def _fold(text):
    return " ".join(
        unicodedata.normalize("NFKC", str(text or ""))
        .casefold()
        .translate(_ASCII_PUNCT)
        .split()
    )


def same_display(a, b):
    fa, fb = _fold(a), _fold(b)
    if not fa or not fb:
        return str(a or "").strip() == str(b or "").strip() and bool(
            str(a or "").strip()
        )
    return fa == fb
