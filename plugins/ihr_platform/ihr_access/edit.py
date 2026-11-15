"""How the front desk edits a patient (P1-T11b2; design-b.md "After the attack" §5.3-§5.4).

A user without CARE's ``can_write_patient_obj`` on the patient who holds
``ihr_edit_patient_demographics`` edits through this rule (views.IhrPatientViewSet):

- **Edit list** (``fields/patient_edit.txt``, verb ``edit``): written, after CARE's own
  validation of those fields (``PatientUpdateSpec``'s validators).
- **Hidden fields** (``hidden``: blood group, extensions, the internal store): always ignored,
  never written. The web app's save resends ``extensions`` filled from the projected GET and
  ``tags: []`` (PatientRegistration.tsx); refusing them would break every save, and accepting
  them would wipe real data.
- **Visible fields not on the edit list** (``visible``: the date of death): ignored when equal to
  the stored value, 400 when changed. Recording a death stays with CARE's write path.
- **Identifiers** in the body keep only configurations whose system is on
  ``fields/identifier_configs.txt``; others are dropped unread (no "not unique" answer for a
  programme number). ``update_identifier`` itself is guarded by identifiers.py.
- Anything else in the body (the web app's form state, unknown keys) is ignored, as CARE does.

``DemographicsSpec`` validates with CARE's ``PatientUpdateSpec`` and writes only the edit list,
so a default of a hidden field (``ExtensionValidator``'s ``{}``) can never be written either.

**Moving a patient's district** (§5.4): a new ``geo_organization`` is accepted only when it lies
in the old one's state or matches the PIN's district (India Post's directory, P1-T7a), and when
its organisation chain gives nobody, through a government organisation, one of
``can_list_patients``, ``can_view_clinical_data`` or ``can_view_questionnaire_responses`` that
the old chain did not already give them (compared per person and slug); otherwise 403 and the
line ``ihr-access: refused geo_move hospital=<slug>``. A patient with no district (CARE sets
it to null when the organisation goes) moves only to the PIN's district, on the same terms.
"""

import functools
from collections import defaultdict

from care.emr.models.organization import Organization, OrganizationUser
from care.emr.resources.patient.spec import PatientUpdateSpec
from care.security.models import RolePermission
from rest_framework.exceptions import PermissionDenied, ValidationError

from ihr_access import branches, guard
from ihr_access import identifiers as identifiers_guard

EDIT_VERBS = frozenset({"edit", "hidden", "visible"})
# The verbs whose fields a demographics edit writes (one place, for the mutation run)
WRITTEN_VERBS = frozenset({"edit"})
# A government organisation member holding one of these reads the patients of its chain
GEO_PRIVILEGED = (
    "can_list_patients",
    "can_view_clinical_data",
    "can_view_questionnaire_responses",
)
CHANGED = "Your role cannot change this field of a patient's record."
GEO_REFUSED = (
    "This move of the patient's district needs a clinical user: it is outside the old "
    "state and the PIN's district, or it opens the record to other people."
)


@functools.cache
def edit_list():
    """{field: verb} from fields/patient_edit.txt."""
    return {
        name: verb
        for name, (verb, _reason) in guard.read_list(
            guard.FIELDS / "patient_edit.txt", verbs=EDIT_VERBS
        ).items()
    }


def written():
    return frozenset(n for n, v in edit_list().items() if v in WRITTEN_VERBS)


def _unchanged(instance, name, value):
    """400 unless ``value`` (as CARE would read it) equals the stored one."""
    parsed = getattr(PatientUpdateSpec.model_validate({name: value}), name)
    stored = instance.external_id if name == "id" else getattr(instance, name, None)
    if parsed == stored:  # a date of death compares as the same instant in any zone
        return
    if parsed is not None and stored is not None and str(parsed) == str(stored):
        return
    raise ValidationError({name: CHANGED})


def allowed_identifiers(identifiers):
    """The body's identifiers less those of a configuration off the allow-list (dropped
    unread). An entry naming no known configuration is left for CARE to refuse. Also used
    for a restricted user's patient create (views.IhrPatientViewSet.clean_create_data)."""
    if not isinstance(identifiers, list):
        return identifiers
    return [
        entry
        for entry in identifiers
        if not identifiers_guard.off_list(
            entry.get("config") if isinstance(entry, dict) else None
        )
    ]


def reduce(instance, data):
    """The body a demographics edit validates: edit-list fields only (see the module)."""
    if not hasattr(data, "items"):
        raise ValidationError("Expected an object.")
    rules = edit_list()
    keep = written()
    body = {}
    for name, value in data.items():
        if name in keep:
            body[name] = value
        elif rules.get(name) == "visible":
            _unchanged(instance, name, value)
        # hidden, or not a field of CARE's update spec: ignored
    if "identifiers" in body:
        body["identifiers"] = allowed_identifiers(body["identifiers"])
    return body


class DemographicsSpec(PatientUpdateSpec):
    """CARE's update spec and its validators; ``de_serialize`` writes the edit list only."""

    def de_serialize(self, obj=None, partial=False):
        if obj is None:
            msg = "a demographics edit updates an existing patient"
            raise ValueError(msg)
        columns = set(self.get_database_mapping())
        dump = self.model_dump(mode="json", exclude_defaults=True, include=written())
        for name, value in dump.items():
            if (
                name in columns
                and name not in self.__exclude__
                and name not in ("id", "external_id")
            ):
                setattr(obj, name, value)
        # CARE's own handling of the place, age or date of birth, PIN and identifiers
        self.perform_extra_deserialization(is_update=True, obj=obj)
        return obj


# --- moving a patient's district -----------------------------------------------------------


def _chain(organization):
    return [*organization.parent_cache, organization.id]


def _state_of(organization):
    return (
        organization.parent_cache[0] if organization.parent_cache else organization.id
    )


def _privileged_grants(organization_ids):
    """(user id, slug) for each GEO_PRIVILEGED slug a member of these government organisations
    holds there. Pairs, not people: a member who already lists patients in the old chain and
    would read their clinical data through a membership in the new one gains something (W3)."""
    slugs_of = defaultdict(set)
    for role_id, slug in RolePermission.objects.filter(
        permission__slug__in=GEO_PRIVILEGED
    ).values_list("role_id", "permission__slug"):
        slugs_of[role_id].add(slug)
    rows = OrganizationUser.objects.filter(
        organization_id__in=list(organization_ids), role_id__in=list(slugs_of)
    ).values_list("user_id", "role_id")
    return {(user_id, slug) for user_id, role_id in rows for slug in slugs_of[role_id]}


def pin_districts(pincode):
    """Ids of this hospital's district organisations India Post places ``pincode`` in."""
    from ihr_platform.models import PostOffice
    from ihr_tenant_settings import geography
    from ihr_tenant_settings.india import GST_STATE_CODES

    if not pincode:
        return set()
    found = set()
    pairs = (
        PostOffice.objects.filter(pincode=str(pincode))
        .values_list("state_code", "district")
        .distinct()
    )
    for code, district in pairs:
        if code not in GST_STATE_CODES:
            continue
        _state, organization = geography.find(code, district)
        if organization is not None:
            found.add(organization.id)
    return found


def geo_move_refused(patient, new, pincode):
    """Why not to move ``patient`` to the government organisation ``new``, or None. A patient
    with no district (CARE's ``SET_NULL``) is placed only by the PIN, and every privileged
    member of the new chain counts as gained unless already on the patient's chain."""
    old = patient.geo_organization
    if old is not None and new.id == old.id:
        return None
    chain = _chain(new)
    placed = bool(pin_districts(pincode) & set(chain)) or (
        old is not None and _state_of(new) == _state_of(old)
    )
    before = patient.organization_cache or (_chain(old) if old is not None else [])
    gained = _privileged_grants(chain) - _privileged_grants(before)
    if placed and not gained:
        return None
    return "geo_move"


def check_geo_move(patient, spec, body):
    """403 (and the refusal line) when the edit moves the patient's district unsafely."""
    if not spec.geo_organization:
        return
    new = Organization.objects.get(external_id=spec.geo_organization)
    pincode = body.get("pincode", patient.pincode)
    if reason := geo_move_refused(patient, new, pincode):
        branches.emit(branches.refusal_line(reason))
        raise PermissionDenied(GEO_REFUSED)
