"""The ECL that CARE's value sets use, and nothing more (P1-T10a1).

CARE sends ECL as a filter {"property": "constraint", "op": "=", "value": "<< 763158003 |Medicinal
product|"}. Only one focus concept with an optional operator and term is accepted:

    <<  X   X and its descendants        <  X   its descendants
    >>  X   X and its ancestors          >  X   its ancestors
        X   X itself                     *      any concept

Everything else (refinements ':', AND/OR/MINUS, member-of '^', nested braces, comments) is refused,
so a value set can never come out wider than written.
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
