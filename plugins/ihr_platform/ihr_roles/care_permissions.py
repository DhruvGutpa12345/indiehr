"""CARE's permissions as last reviewed (P1-T11b1, review C6): ``care_permissions.txt``.

After an upstream sync, ``registry.ORIGINAL`` is rebuilt from the new code, so comparing with
it only proves that our grants left CARE's role lists alone. This snapshot is what catches
upstream drift: every CARE permission slug with its context, its class for the restricted rule
and CARE's own role names. ``ihr_roles.tests.test_grants.CarePermissionsReviewed`` fails on a new,
removed or changed slug until the line is reviewed here:

- ``clinical``: in ``registry.CLINICAL_SLUGS`` (opens clinical data; holding it anywhere makes a
  person non-restricted, and no restricted role is granted it);
- ``opening``: in ``registry.PATIENT_OPENING`` (opens or writes a patient or an encounter);
- ``other``: neither.

A new clinical slug left as ``other`` would let a person who holds it count as restricted (the
guard then narrows what they see: fails closed) and would show it in a restricted user's patient
``permissions``; so classify before you accept the line.
"""

from pathlib import Path

PATH = Path(__file__).with_name("care_permissions.txt")
CLASSES = ("clinical", "opening", "other")


def live():
    """{slug: (context, sorted CARE role names)} from CARE's code, without our role names.
    A slug registered twice with different values maps to None."""
    from care.security.permissions.base import PermissionController

    from ihr_roles.registry import PERMANENT_NAMES

    ours = set(PERMANENT_NAMES)
    found = {}
    for handler in PermissionController.internal_permission_handlers:
        for perm in handler:
            value = (
                getattr(perm.value.context, "value", str(perm.value.context)),
                tuple(sorted(r.name for r in perm.value.roles if r.name not in ours)),
            )
            if perm.name in found and found[perm.name] != value:
                found[perm.name] = None
            else:
                found.setdefault(perm.name, value)
    return found


def classify(slug):
    from ihr_roles.registry import CLINICAL_SLUGS, PATIENT_OPENING

    if slug in CLINICAL_SLUGS:
        return "clinical"
    if slug in PATIENT_OPENING:
        return "opening"
    return "other"


def line(slug, context, roles, cls):
    # No trailing space for a permission no CARE role holds (the whitespace hook would strip it)
    return f"{slug} | {context} | {cls} | {', '.join(roles)}".rstrip()


def read(path=PATH):
    """{slug: (context, roles tuple, class)}; ValueError on a malformed line."""
    entries = {}
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        parts = [p.strip() for p in text.split("|")]
        if len(parts) != 4 or parts[2] not in CLASSES or parts[0] in entries:  # noqa: PLR2004
            msg = f"{path.name}:{number}: malformed or repeated line"
            raise ValueError(msg)
        slug, context, cls, roles = parts
        entries[slug] = (
            context,
            tuple(r.strip() for r in roles.split(",") if r.strip()),
            cls,
        )
    return entries
