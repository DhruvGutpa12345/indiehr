"""Our classes in place of CARE's for the restricted roles (P1-T11b2; docs/reviews/p1-t11/
design-b.md §5.3 and "After the attack" §5-§7).

ihr_platform.route_swaps registers each of these in place of the CARE class it subclasses
(``SWAPS``), so every route of it reaches ours: list and detail, format suffixes and batch
sub-requests; ihr_platform.E020 refuses to serve if a swap did not take.

- **Patients** (``IhrPatientViewSet``): CARE's path first. Where it gives nothing, a holder of
  ``ihr_view_patient_demographics`` opens any patient of this hospital (Q-K34), for the actions
  of ``fields/patient_actions.txt`` marked ``widened`` only, and edits with
  ``ihr_edit_patient_demographics`` under the edit rule (edit.py). Other actions answer 403 to
  them. Gates on CARE's own actions: search (Q-K40), ``add_user`` with a restricted target, and
  a patient's bookings and tokens only with ``?facility``.
- **Encounters** (``IhrEncounterViewSet``): for ``?patient`` and ``?facility``, CARE's path
  answers, unprojected, when the user holds CARE's right there; otherwise our branch serves only
  the rows ``ihr_view_encounter_status`` allows, with the filter allow-list
  (``fields/encounter_filters.txt``: 400 on any other parameter). Retrieve: CARE's
  ``authorize_retrieve`` first, then our slug. Create: restricted users cannot set clinical
  fields.
- **Refused to restricted users**: bed history (location associations), account reports and
  every report generation (Q-K41).
- **Closed to restricted users and to users of our roles only** (``branches.closed``, the
  Radiologist included; P1-T11b3's matrix found CARE's membership-only lists showing the
  Radiologist other patients, Q-K44 A): resource requests and their comments, device retrieve
  and actions (the list is empty); delivery orders and supply deliveries linked to a patient
  are absent and cannot be made (Q-K43); **dispense orders** (each names its patient): an empty
  list, 403 otherwise.

Every widening branch calls ``branches.widen()``: the rest of the request is projected.
"""

import copy
import functools

from care.emr.api.viewsets.device import DeviceViewSet
from care.emr.api.viewsets.encounter import EncounterFilters, EncounterViewSet
from care.emr.api.viewsets.inventory.delivery_order import DeliveryOrderViewSet
from care.emr.api.viewsets.inventory.dispense_order import DispenseOrderViewSet
from care.emr.api.viewsets.inventory.supply_delivery import SupplyDeliveryViewSet
from care.emr.api.viewsets.location import FacilityLocationEncounterViewSet
from care.emr.api.viewsets.patient import PatientViewSet
from care.emr.api.viewsets.report.report_upload import ReportUploadViewSet
from care.emr.api.viewsets.resource_request import (
    ResourceRequestCommentViewSet,
    ResourceRequestViewSet,
)
from care.emr.api.viewsets.scheduling.availability import (
    SlotsForDayRequestSpec,
    SlotViewSet,
)
from care.emr.api.viewsets.scheduling.schedule import get_schedulable_resource
from care.emr.models import Patient
from care.emr.models.device import Device
from care.emr.models.medication_dispense import DispenseOrder
from care.emr.models.supply_delivery import DeliveryOrder
from care.emr.resources.encounter.spec import EncounterCreateSpec
from care.facility.models import Facility
from care.security.authorization import AuthorizationController
from care.users.models import User
from care.utils.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.http import Http404
from django_filters import rest_framework as filters
from django_filters.utils import translate_validation
from pydantic import BaseModel
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from ihr_access import branches, edit, guard, identifiers, state

# --- patients ------------------------------------------------------------------------------

ACTION_VERBS = frozenset({"widened", "gated", "care"})
SEARCHES = frozenset({"search", "search_retrieve"})
BOOKINGS = frozenset({"get_appointments", "get_tokens"})
SEARCH_REFUSED = "Your role cannot search patients."
NOT_ON_THIS_RECORD = "Your role cannot do this on a patient's record."
RESTRICTED_TARGET = (
    "A front-office, billing, records or store role cannot be given a patient's record."
)
BOOKINGS_NEED_FACILITY = "Name the facility (?facility=) to list a patient's bookings."
CREATE_REFUSED = "Cannot Create Patient"  # CARE's own words


@functools.cache
def actions(name):
    """{action: verb} from fields/<name>_actions.txt."""
    return {
        action: verb
        for action, (verb, _reason) in guard.read_list(
            guard.FIELDS / f"{name}_actions.txt", verbs=ACTION_VERBS
        ).items()
    }


def widened(name):
    return frozenset(a for a, v in actions(name).items() if v == "widened")


OPENED = widened("patient")  # our get_object serves these (retrieve, update...)
EDITED = OPENED - {"retrieve"}  # our authorize_update serves these


class IhrPatientViewSet(PatientViewSet):
    ihr_opened = False  # our get_object branch served this request
    ihr_edit_branch = False  # our edit rule served this write (handle_update)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if self.action in SEARCHES:
            self._search_gate(request.user)
        elif self.action == "add_user":
            self._refuse_restricted_target(request)
        elif self.action in BOOKINGS and branches.restricted(request.user):
            self._bookings_of_one_facility(request)

    def _search_gate(self, user):
        gate = branches.search_gate(user)
        if gate == branches.REFUSED:
            raise PermissionDenied(SEARCH_REFUSED)
        if gate == branches.PROJECTED:
            branches.widen()

    def _refuse_restricted_target(self, request):
        """A care-team row would give a restricted person the patient's clinical record (Q-K40;
        the placement trigger refuses the row too, as 400)."""
        data = request.data if hasattr(request.data, "get") else {}
        target = None
        if data.get("user"):
            try:
                target = User.objects.filter(external_id=data.get("user")).first()
            except (DjangoValidationError, ValueError, TypeError):
                target = None  # not an id: CARE refuses the request itself
        if target is not None and state.restricted(target):
            raise PermissionDenied(RESTRICTED_TARGET)

    def _bookings_of_one_facility(self, request):
        """Restricted users name the facility, so CARE's own booking or token check runs;
        what they then see is projected. A patient under their own live records grant is
        CARE's to answer (MRD, P1-T11b4)."""
        if request.GET.get("facility"):
            branches.widen()
            return
        try:
            patient = (
                Patient.objects.only("id")
                .filter(external_id=self.kwargs.get(self.lookup_field))
                .first()
            )
        except (DjangoValidationError, ValueError, TypeError):
            patient = None
        if (
            patient is None
            or patient.pk not in state.for_user(request.user).granted_patients
        ):
            raise PermissionDenied(BOOKINGS_NEED_FACILITY)

    def _widened(self, which):
        """This request's action is one our branch serves (the per-action allow-list)."""
        return self.action in which

    def get_object(self):
        try:
            found = super().get_object()
        except Http404:
            if not branches.holds(self.request.user, branches.DEMOGRAPHICS):
                raise
            patient = get_object_or_404(
                Patient.objects.select_related(
                    "created_by", "updated_by", "geo_organization"
                ),
                external_id=self.kwargs[self.lookup_field],
            )
            if not self._widened(OPENED):
                raise PermissionDenied(NOT_ON_THIS_RECORD) from None
            self.ihr_opened = True
            branches.widen()
            return patient
        self.ihr_object = found
        return found

    def get_serializer_retrieve_context(self):
        """CARE's retrieve refuses ``?facility`` (which the web app always sends) without
        ``can_read_tag_config``; the projection drops facility tags anyway, so our branch
        checks only that the facility exists (review C1). MRD reading a patient under its own
        live records grant (CARE's path, unprojected) gets that facility's tags and numbers
        as a clinician does, where it holds the records right (P1-T11b4)."""
        if (
            self.ihr_opened or self.ihr_edit_branch
        ):  # our branch: no facility tags to show
            if facility := self.request.GET.get("facility"):
                get_object_or_404(Facility, external_id=facility)
            return {"facility": None}
        try:
            return super().get_serializer_retrieve_context()
        except PermissionDenied:
            facility = self._granted_facility()
            if facility is None:
                raise
            return {"facility": facility}

    def _granted_facility(self):
        """The ``?facility`` of a retrieve of a patient under this user's own live records
        grant, where the user holds the records right; else None."""
        from ihr_access import records

        patient = getattr(self, "ihr_object", None)
        user = self.request.user
        value = self.request.GET.get("facility")
        if (
            self.action != "retrieve"
            or patient is None
            or not value
            or patient.pk not in state.for_user(user).granted_patients
        ):
            return None
        facility = get_object_or_404(Facility, external_id=value)
        if not branches.holds(user, records.REQUEST_SLUG, facility=facility):
            return None
        return facility

    def _edits_demographics(self, patient):
        """Our edit rule serves this write (computed once per patient and request)."""
        decided = self.__dict__.setdefault("_ihr_edits", {})
        if patient.pk not in decided:
            user = self.request.user
            # the cheap tests first: a user of CARE's roles only never pays CARE's write check
            # twice (review C3; branches.may_hold reads this request's cached roles)
            decided[patient.pk] = (
                self._widened(EDITED)
                and branches.may_hold(user, branches.EDIT)
                and not AuthorizationController.call(
                    "can_write_patient_obj", user, patient
                )
                and branches.holds(user, branches.EDIT)
            )
        return decided[patient.pk]

    def authorize_update(self, request_obj, model_instance):
        if self._edits_demographics(model_instance):
            # update_identifier on our branch keeps to the identifier list, whatever else
            # the user holds (review W2; the PUT path drops such entries: edit.reduce)
            if self.action == "update_identifier" and identifiers.off_list(
                identifiers.requested_config(self.request.data)
            ):
                raise ValidationError(identifiers.REFUSED_WRITE)
            branches.widen()
            return
        super().authorize_update(request_obj, model_instance)

    def handle_update(self, instance, request_data):
        if not self._edits_demographics(instance):
            if self.ihr_opened or branches.restricted(self.request.user):
                # CARE validates the body (a programme number's "not unique" included) before
                # it authorizes: decide first, so the answer never depends on the body
                # (reviews R1, W1; also CARE's upsert, which finds the patient by id)
                self.authorize_update(None, instance)
            return super().handle_update(instance, request_data)
        body = edit.reduce(instance, self.clean_update_data(request_data))
        context = {
            "is_update": True,
            "object": instance,
            **self.get_serializer_update_context(),
        }
        spec = edit.DemographicsSpec.model_validate(body, context=context)
        spec._context = context  # noqa: SLF001 (as CARE's handle_update)
        self.validate_data(spec, instance)
        self.authorize_update(spec, instance)
        edit.check_geo_move(instance, spec, body)
        model_instance = spec.de_serialize(obj=instance)
        self.perform_update(model_instance)
        # CARE's handle_update answers through serialize_retrieve (the user and the retrieve
        # context, P2-T0a); on our branch that context is ours: no facility tags to show
        self.ihr_edit_branch = True
        return self.serialize_retrieve(instance)

    def clean_create_data(self, request_data):
        """A restricted user's create (POST and CARE's upsert): CARE validates the body,
        identifier uniqueness included, before ``can_create_patient``. Refuse first; then keep
        to the identifier list and drop extensions and meta, as the edit rule does (reviews
        W1, W4).
        Tags are CARE's ``can_apply_tag_config``, which no restricted role holds."""
        data = super().clean_create_data(request_data)
        user = self.request.user
        if not branches.restricted(user):
            return data
        if not AuthorizationController.call("can_create_patient", user):
            raise PermissionDenied(CREATE_REFUSED)
        if not hasattr(data, "items"):
            return data  # CARE refuses it
        data = dict(data.items())
        if "identifiers" in data:
            data["identifiers"] = edit.allowed_identifiers(data["identifiers"])
        for hidden in ("extensions", "meta"):  # hidden in fields/patient_edit.txt too
            data.pop(hidden, None)
        return data


# --- encounters ----------------------------------------------------------------------------

FILTER_VERBS = frozenset({"allow", "refuse"})
# Parameters the list reads that are not filters of CARE's EncounterFilters
LIST_PARAMETERS = frozenset(
    {"patient", "tags", "tags_behavior", "ordering", "limit", "offset"}
)
FILTERS_REFUSED = "These filters are not available to your role: {}"
CREATE_VERBS = frozenset({"allow", "clinical", "hidden"})
CLINICAL_REFUSED = "Your role cannot set these clinical fields: {}"
EMPTY = (None, "", [], {})


@functools.cache
def filter_rules():
    """{parameter: allow|refuse} from fields/encounter_filters.txt."""
    return {
        name: verb
        for name, (verb, _reason) in guard.read_list(
            guard.FIELDS / "encounter_filters.txt", verbs=FILTER_VERBS
        ).items()
    }


@functools.cache
def projected_filters():
    """CARE's encounter filters that fields/encounter_filters.txt allows, and no tag filter."""
    allowed = {n for n, v in filter_rules().items() if v == "allow"}
    declared = {
        name: copy.deepcopy(f)
        for name, f in EncounterFilters.base_filters.items()
        if name in allowed
    }
    return type("IhrProjectedEncounterFilters", (filters.FilterSet,), declared)


@functools.cache
def allowed_parameters():
    """The query parameters our branch accepts (a range filter reads name_after/_before)."""
    names = set()
    for name, verb in filter_rules().items():
        if verb != "allow":
            continue
        found = EncounterFilters.base_filters.get(name)
        widget = getattr(getattr(found, "field", None), "widget", None)
        suffixes = getattr(widget, "suffixes", None)
        if suffixes:
            names |= {f"{name}_{s}" if s else name for s in suffixes}
        else:
            names.add(name)
    return frozenset(names)


def filter_problems():
    """Why the filter allow-list does not cover CARE's encounter list (ihr_access.E024)."""
    rules = filter_rules()
    care = set(EncounterFilters.base_filters)
    problems = [
        f"CARE's encounter filter {name!r} is not classified in fields/encounter_filters.txt"
        for name in sorted(care - set(rules))
    ]
    problems += [
        f"fields/encounter_filters.txt names {name!r}, which CARE's encounter list no "
        "longer reads"
        for name in sorted(set(rules) - care - LIST_PARAMETERS)
    ]
    return problems


@functools.cache
def create_rules():
    """{field path: allow|clinical|hidden} from fields/encounter_create.txt."""
    return {
        name: verb
        for name, (verb, _reason) in guard.read_list(
            guard.FIELDS / "encounter_create.txt", verbs=CREATE_VERBS
        ).items()
    }


def _nested_model(annotation):
    """The pydantic model a field holds (``HospitalizationSpec | None`` -> the spec)."""
    for candidate in (annotation, *getattr(annotation, "__args__", ())):
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def spec_paths(model, prefix=""):
    """Every field path of ``model``, nested models' fields dotted."""
    paths = set()
    for name, info in model.model_fields.items():
        paths.add(prefix + name)
        if nested := _nested_model(info.annotation):
            paths |= spec_paths(nested, f"{prefix}{name}.")
    return paths


def create_problems():
    """Why fields/encounter_create.txt does not match CARE's create spec (review C2)."""
    rules, care = create_rules(), spec_paths(EncounterCreateSpec)
    return [
        f"CARE's encounter create field {name!r} is not classified in "
        "fields/encounter_create.txt"
        for name in sorted(care - set(rules))
    ] + [
        f"fields/encounter_create.txt names {name!r}, which CARE's create spec no longer has"
        for name in sorted(set(rules) - care)
    ]


def refuse_clinical_fields(data):
    """In place, for a restricted user's create body: drop ``hidden`` fields; 400 when a field
    not marked ``allow`` (clinical, or one CARE added and nobody classified) has a value."""
    rules, known = create_rules(), spec_paths(EncounterCreateSpec)
    found = []

    def walk(body, prefix):
        for key in list(body):
            path = prefix + str(key)
            if path not in known:
                continue  # not a field of CARE's spec: CARE ignores it
            verb = rules.get(path)
            if verb == "hidden":
                del body[key]
            elif verb == "allow":
                if isinstance(body[key], dict):
                    walk(body[key], path + ".")
            elif body[key] not in EMPTY:
                found.append(path)

    walk(data, "")
    if found:
        raise ValidationError(CLINICAL_REFUSED.format(", ".join(sorted(found))))


def in_units(organizations):
    """Encounters filed under one of these facility organisations (or in a bed of one); the
    caches hold parents, so the root covers the whole facility."""
    return Q(facility_organization_cache__overlap=organizations) | Q(
        current_location__facility_organization_cache__overlap=organizations
    )


class IhrEncounterViewSet(EncounterViewSet):
    ihr_status_branch = False

    def _status_holder(self, facility=None):
        user = self.request.user
        return branches.holds(user, branches.DEMOGRAPHICS) and branches.holds(
            user, branches.STATUS, facility
        )

    def _rows(self):
        """CARE's rows before its list checks (EncounterViewSet.get_queryset, pinned in
        care_copies.txt; CareCopies.test_our_rows_are_cares_rows compares the SQL)."""
        return (
            super(EncounterViewSet, self)
            .get_queryset()
            .select_related(
                "patient",
                "facility",
                "appointment",
                "current_location",
                "created_by",
                "updated_by",
            )
            .order_by("-created_date")
        )

    def get_queryset(self):
        if self.action == "list":
            rows = self._status_rows()
            if rows is not None:
                return rows
        return super().get_queryset()

    def _status_rows(self):
        """Our branch's rows, or None when CARE's path answers (a user with CARE's right there
        gets CARE's rows, unprojected, and nothing of ours added: "After the attack" §6.3).
        Rows follow the units where the user holds the status slug, as CARE's own encounter
        check does (review R3)."""
        user = self.request.user
        params = self.request.GET
        if params.get("patient"):
            if not self._status_holder():
                return None  # CARE answers (or refuses); checked first, it costs least
            patient = get_object_or_404(Patient, external_id=params["patient"])
            if AuthorizationController.call("can_view_patient_obj", user, patient):
                return None
            organizations = branches.organizations_holding(user, branches.STATUS)
            rows = self._rows().filter(patient=patient).filter(in_units(organizations))
        elif params.get("facility"):
            facility = get_object_or_404(Facility, external_id=params["facility"])
            if branches.holds(user, "can_list_encounter", facility):
                return None
            if not self._status_holder(facility):
                return None  # CARE's list: empty for them
            organizations = branches.organizations_holding(
                user, branches.STATUS, facility
            )
            rows = (
                self._rows().filter(facility=facility).filter(in_units(organizations))
            )
        else:
            return None
        self.ihr_status_branch = True
        self._refuse_other_parameters()
        branches.widen()
        return rows

    def _refuse_other_parameters(self):
        if extra := sorted(set(self.request.GET) - allowed_parameters()):
            raise ValidationError(FILTERS_REFUSED.format(", ".join(extra)))

    def filter_queryset(self, queryset):
        if not self.ihr_status_branch:
            return super().filter_queryset(queryset)
        filterset = projected_filters()(
            self.request.GET, queryset=queryset, request=self.request
        )
        if not filterset.is_valid():
            raise translate_validation(filterset.errors)
        return OrderingFilter().filter_queryset(self.request, filterset.qs, self)

    def _status_retrievable(self, encounter):
        """Both slugs in the encounter's facility, the status slug in one of its units (its
        departments' or its bed's organisations, as CARE's ``can_view_encounter_obj``)."""
        if not self._status_holder(encounter.facility):
            return False
        organizations = [*encounter.facility_organization_cache]
        if encounter.current_location:
            organizations += encounter.current_location.facility_organization_cache
        return branches.holds_in(self.request.user, branches.STATUS, organizations)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            self.authorize_retrieve(instance)
        except PermissionDenied:
            if not self._status_retrievable(instance):
                raise
            branches.widen()
        data = self.serialize_retrieve(
            instance
        )  # CARE's (P2-T0a: the user and context)
        return Response(data)

    def clean_create_data(self, request_data):
        data = super().clean_create_data(request_data)
        if branches.restricted(self.request.user) and hasattr(data, "items"):
            data = copy.deepcopy(dict(data.items()))
            refuse_clinical_fields(data)
        return data


# --- refused to restricted users -----------------------------------------------------------

BED_HISTORY_REFUSED = "Your role cannot see who was in a bed."
RESOURCE_REQUESTS_REFUSED = "Your role cannot see resource requests."
DEVICES_REFUSED = "Your role cannot open devices."
REPORTS_REFUSED = "Your role cannot open or make these reports."
ACCOUNT_REPORT = "account_report"


class IhrLocationEncounterViewSet(FacilityLocationEncounterViewSet):
    """Bed history: each row embeds the whole encounter (location/spec.py)."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if branches.restricted(request.user):
            raise PermissionDenied(BED_HISTORY_REFUSED)


class _ResourceRequestGate:
    """CARE checks no permission on resource requests beyond membership, and they name the
    related patient (resource_request.py): closed to ``branches.closed`` users."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if branches.closed(request.user):
            raise PermissionDenied(RESOURCE_REQUESTS_REFUSED)


class IhrResourceRequestViewSet(_ResourceRequestGate, ResourceRequestViewSet):
    pass


class IhrResourceRequestCommentViewSet(
    _ResourceRequestGate, ResourceRequestCommentViewSet
):
    pass


class IhrDeviceViewSet(DeviceViewSet):
    """CARE lists and opens devices for any member of the facility, with the patient on each
    (A2). None of our roles has a device job: for ``branches.closed`` users an empty list, 403
    for everything else."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if self.action != "list" and branches.closed(request.user):
            raise PermissionDenied(DEVICES_REFUSED)

    def get_queryset(self):
        if branches.closed(self.request.user):
            return Device.objects.none()
        return super().get_queryset()


class IhrReportUploadViewSet(ReportUploadViewSet):
    """An account report may print the whole clinical encounter (Q-K41): restricted users
    cannot list, open, archive or generate one, and generate nothing at all (the patient,
    encounter and discharge-summary reports are closed to them by CARE's authorizers; an
    instance template has no facility check)."""

    def _refused(self):
        return branches.restricted(self.request.user)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not self._refused():
            return
        listing_accounts = (
            self.action == "list" and request.GET.get("report_type") == ACCOUNT_REPORT
        )
        if self.action == "generate" or listing_accounts:
            raise PermissionDenied(REPORTS_REFUSED)

    def get_object(self):
        report = super().get_object()
        if report.report_type == ACCOUNT_REPORT and self._refused():
            raise PermissionDenied(REPORTS_REFUSED)
        return report


def without_patients(queryset, path):
    """Rows not linked to a patient (a medication return names the patient: Q-K43)."""
    return queryset.filter(**{f"{path}__isnull": True})


PATIENT_LINK_REFUSED = "Your role cannot link a delivery to a patient."
DISPENSE_REFUSED = "Your role cannot see medicines dispensed to patients."


class _NoPatientRows:
    patient_path = None

    def get_queryset(self):
        queryset = super().get_queryset()
        if branches.closed(self.request.user):
            queryset = without_patients(queryset, self.patient_path)
        return queryset

    def handle_update(self, instance, request_data):
        # CARE's upsert finds rows by id without get_queryset
        linked = instance.__class__.objects.filter(pk=instance.pk)
        if (
            branches.closed(self.request.user)
            and not without_patients(linked, self.patient_path).exists()
        ):
            raise Http404
        return super().handle_update(instance, request_data)

    def clean_create_data(self, request_data):
        """A create (POST and CARE's upsert) runs before CARE's validation: a restricted user
        cannot make a patient-linked row either, whose answer would name the patient
        (review W5)."""
        data = super().clean_create_data(request_data)
        if branches.closed(self.request.user) and hasattr(data, "get"):
            self.refuse_patient_link(data)
        return data


class IhrDeliveryOrderViewSet(_NoPatientRows, DeliveryOrderViewSet):
    patient_path = "patient"

    def refuse_patient_link(self, data):
        if data.get("patient"):
            raise PermissionDenied(PATIENT_LINK_REFUSED)


class IhrSupplyDeliveryViewSet(_NoPatientRows, SupplyDeliveryViewSet):
    patient_path = "order__patient"

    def refuse_patient_link(self, data):
        """An order linked to a patient answers as if it did not exist (as handle_update)."""
        if not (order := data.get("order")):
            return  # CARE refuses it
        try:
            found = DeliveryOrder.objects.filter(external_id=order)
            if found.exists() and not without_patients(found, "patient").exists():
                raise Http404
        except (DjangoValidationError, ValueError, TypeError):
            return  # not an id: CARE refuses it


class IhrDispenseOrderViewSet(DispenseOrderViewSet):
    """Every dispense order names the patient who received the medicines, and CARE opens them
    to anyone holding the store's supply-delivery permissions at the location, which the Store
    Keeper holds (review R2, Q-K43 A): restricted users get an empty list and 403 for
    everything else (retrieve, create, update, upsert)."""

    def _closed(self):
        user = self.request.user
        return branches.closed(user)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if self.action != "list" and self._closed():
            raise PermissionDenied(DISPENSE_REFUSED)

    def get_queryset(self):
        if self._closed():
            return DispenseOrder.objects.none()
        return super().get_queryset()


class IhrSlotViewSet(SlotViewSet):
    """CARE's ``get_slots_for_day`` checks no right ("TODO: Add Authorization"): every member of
    the hospital read any doctor's or room's day, its schedule included, though only the front
    desk holds the booking right (P1-T11b3 review fixes: the matrix's POST reads). For
    ``branches.closed`` users it now answers as its sibling ``availability_stats`` does: the
    resource's read right (CARE's ``authorize_resource_read``, ``can_list_booking``) first.
    CARE's own roles are unchanged (Q-K40's list)."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if self.action == "get_slots_for_day" and branches.closed(request.user):
            data = SlotsForDayRequestSpec(**request.data)
            facility = get_object_or_404(
                Facility, external_id=self.kwargs["facility_external_id"]
            )
            resource = get_schedulable_resource(
                data.resource_type, data.resource_id, facility
            )
            if not resource:
                raise ValidationError("No schedules found for this resource")
            self.authorize_resource_read(resource)

    def finalize_response(self, request, response, *args, **kwargs):
        """CARE's ``create_appointment`` answers the configured actions' outputs in
        ``_actions``; for ``branches.closed`` users no action runs (actions.py) and the key is
        not sent (P2-T0a, review F2)."""
        data = getattr(response, "data", None)
        if (
            self.action == "create_appointment"
            and isinstance(data, dict)
            and branches.closed(request.user)
        ):
            data.pop("_actions", None)
        return super().finalize_response(request, response, *args, **kwargs)
