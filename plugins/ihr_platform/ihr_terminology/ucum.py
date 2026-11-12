"""Is this a UCUM unit we can accept? (P1-T10a1)

UCUM is a grammar, not a list: "{capsule}", "{breaths}/min" and "mg/kg/d" are all valid. Expansion
offers the common units (FHIR's ucum-common value set); validation also accepts a unit built from
them by this subset of UCUM's syntax:

    unit       := ["/"] component (("." | "/") component)*
    component  := annotation | number | atom [exponent] [annotation]
    annotation := "{" ASCII 33-126 except braces "}"
    number     := digits | "10*" digits | "10^" digits    (ASCII digits only)
    atom       := a code of the loaded common-units list (exponent: an optional sign and digits)

Parentheses and other rarer forms are refused (pick them from the list instead).
"""

import re

MAX_UNIT_LENGTH = 128
_ANNOTATION = r"\{[!-z|~]*\}"


def _component_ok(part, atoms):
    if re.fullmatch(_ANNOTATION, part) or re.fullmatch(
        r"10[*^][+-]?[0-9]+|[0-9]+", part
    ):
        return True
    m = re.fullmatch(rf"(.+?)([+-]?[0-9]+)?({_ANNOTATION})?", part)
    if not m:
        return False
    atom = m.group(1)
    if atom in atoms:
        return True
    # an atom that itself ends in digits (e.g. listed "m2"): try the whole part before the annotation
    whole = part[: m.start(3)] if m.group(3) else part
    return whole in atoms


def is_valid(code, atoms):
    """atoms: the set of codes in the loaded common-units list."""
    code = str(code or "")
    if (
        not code
        or len(code) > MAX_UNIT_LENGTH
        or not code.isascii()
        or any(c.isspace() for c in code)
        or "(" in code
        or ")" in code
    ):
        return False
    if code in atoms:
        return True
    body = code.removeprefix("/")
    # split on "." and "/" outside annotations
    parts, depth, cur = [], 0, ""
    for c in body:
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                return False
        if c in "./" and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    parts.append(cur)
    if depth != 0:
        return False
    return all(p and _component_ok(p, atoms) for p in parts)
