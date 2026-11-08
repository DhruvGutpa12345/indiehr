"""Indian states and districts as CARE government organisations, per hospital (P1-T7a).

CARE keeps a patient's and a user's place as a "govt" organisation. We fill each hospital's
schema with every state and district the PIN directory names, in the same shape as CARE's
own loader (``load_govt_organization_csv``), so a later full load of local bodies finds
these rows instead of adding duplicates.
"""

import difflib

from django.db import transaction

from ihr_tenant_settings.india import GST_STATE_CODES

STATE_META = {
    "country": "India",
    "govt_org_type": "state",
    "govt_org_children_type": "district",
}
DISTRICT_META = {
    "country": "India",
    "govt_org_type": "district",
    "govt_org_children_type": "local_body",
}


# A state or district already in the hospital is reused whatever its metadata: rows made
# through CARE's own API, screens or fixtures have none, and must not be duplicated.


def ensure_state(code):
    from care.emr.models.organization import Organization

    name = GST_STATE_CODES[code]
    state = (
        Organization.objects.filter(name__iexact=name, org_type="govt", parent=None)
        .order_by("id")
        .first()
    )
    if state is None:
        state = Organization.objects.create(
            name=name, org_type="govt", system_generated=True, metadata=STATE_META
        )
    return state


def ensure_district(state, name):
    from care.emr.models.organization import Organization

    district = (
        Organization.objects.filter(name__iexact=name, parent=state, org_type="govt")
        .order_by("id")
        .first()
    )
    if district is not None:
        return district, False
    district = Organization.objects.create(
        name=name,
        org_type="govt",
        parent=state,
        system_generated=True,
        metadata=DISTRICT_META,
    )
    return district, True


def directory_district(code, name):
    """The PIN directory's spelling of a district typed by a person, or the typed name if
    the directory is not loaded or does not know it."""
    from ihr_platform.models import PostOffice

    known = (
        PostOffice.objects.filter(state_code=code, district__iexact=name.strip())
        .values_list("district", flat=True)
        .first()
    )
    return known or name.strip()


def district_problem(code, name):
    """Why a typed district cannot be used for this state, or None. Once the PIN directory
    is loaded, the district must be one it names, so a hospital never ends up with two
    organisations for one district ("Gurgaon" typed, "Gurugram" in the directory)."""
    from ihr_platform.models import PostOffice

    known = list(
        PostOffice.objects.filter(state_code=code)
        .values_list("district", flat=True)
        .distinct()
    )
    if not known:  # directory not loaded, or it has no offices in this state
        return None
    if any(d.lower() == name.strip().lower() for d in known):
        return None
    msg = f"not in India Post's list for {GST_STATE_CODES[code]}"
    if close := difflib.get_close_matches(name.strip().title(), known, n=3, cutoff=0.6):
        msg += f" (did you mean {', '.join(close)}?)"
    return msg


def load_all():
    """Every state and district in the PIN directory, in the current hospital's schema.
    Safe to repeat. Returns (states, districts added)."""
    from ihr_platform.models import PostOffice

    pairs = (
        PostOffice.objects.values_list("state_code", "district")
        .distinct()
        .order_by("state_code", "district")
    )
    states, added = {}, 0
    with transaction.atomic():
        for code, district in pairs:
            if code not in states:
                states[code] = ensure_state(code)
            added += ensure_district(states[code], district)[1]
    return len(states), added


def find(code, district):
    """The hospital's organisations for a state and district: (state, district or None)."""
    from care.emr.models.organization import Organization

    state = (
        Organization.objects.filter(
            name__iexact=GST_STATE_CODES[code], org_type="govt", parent=None
        )
        .order_by("id")
        .first()
    )
    if state is None:
        return None, None
    return state, (
        Organization.objects.filter(
            name__iexact=district, parent=state, org_type="govt"
        )
        .order_by("id")
        .first()
    )
