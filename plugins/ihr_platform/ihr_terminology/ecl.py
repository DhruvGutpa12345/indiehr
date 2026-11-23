"""The ECL that CARE's value sets use, and nothing more (P1-T10a1).

CARE sends ECL as a filter {"property": "constraint", "op": "=", "value": "<< 763158003 |Medicinal
product|"}. Only one focus concept with an optional operator and term is accepted:

    <<  X   X and its descendants        <  X   its descendants
    >>  X   X and its ancestors          >  X   its ancestors
        X   X itself                     *      any concept

One ``MINUS`` between two such focus expressions is accepted too (``parse_expression``): CARE
3.1's system-symptom-code says ``< 404684003 MINUS << 64572001`` (P2-T0a). MINUS only takes
concepts away, so it never widens a set. Everything else (refinements ':', AND/OR, a second
MINUS, '*' beside MINUS, member-of '^', brackets, nested braces, comments) is refused, so a
value set can never come out wider than written.
"""

import re

_ECL = re.compile(r"\s*(<<|<|>>|>)?\s*(\d{6,18}|\*)\s*(\|[^|]*\|)?\s*")


class EclError(ValueError):
    pass


def parse(text):
    """-> (operator, code): operator one of '<<', '<', '>>', '>', '' ; code a digit string or '*'."""
    m = _ECL.fullmatch(str(text or ""))
    if not m:
        msg = f"ECL beyond the supported subset: {str(text)[:80]!r}"
        raise EclError(msg)
    op, code = m.group(1) or "", m.group(2)
    if code == "*" and op:
        msg = "an operator with '*' is not supported"
        raise EclError(msg)
    return op, code


_MINUS = re.compile(r"\s+MINUS\s+")


def parse_expression(text):
    """-> ((operator, code), (operator, code) or None): the focus, and the focus taken away
    from it (``A MINUS B``), each as ``parse`` reads it; EclError beyond that."""
    parts = _MINUS.split(str(text or ""))
    if len(parts) > 2:  # noqa: PLR2004 (A MINUS B, nothing longer)
        msg = f"ECL beyond the supported subset: {str(text)[:80]!r}"
        raise EclError(msg)
    focus = parse(parts[0])
    if len(parts) == 1:
        return focus, None
    taken = parse(parts[1])
    if "*" in (focus[1], taken[1]):
        msg = "'*' is not supported beside MINUS"
        raise EclError(msg)
    return focus, taken
