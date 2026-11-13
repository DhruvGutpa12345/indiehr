"""CARE's routes that answer without a staff sign-in, trimmed to what the web app shows (P1-T20a).

Design and attack: docs/reviews/p1-t20/design.md ("After the attack"). These classes replace
CARE's in its router (``route_swaps``), so every variant of each route gets them: list and
detail, DEBUG's format suffixes, and batch sub-requests.

- The doctor list of a facility (anonymous; the patient portal shows it before any sign-in):
  only public facilities, only practitioners with a public schedule still valid, only active,
  not deleted staff; each one as id, names, title and photo (``PUBLIC_PRACTITIONER_KEYS``). CARE
  sends username, phone number, last sign-in, gender and roles, for any facility.
- The patient-OTP appointment routes (anyone who can receive an OTP): each booking built from
  the model by ``PublicTokenBookingSpec`` (never CARE's booking serialiser, which copies staff
  records, another patient's encounter and, on a cold cache, saves the location's parent copy),
  then ``public_booking`` as a second allow-list. The doctor is the same projection; a location
  or healthcare service is its name and kind only (``RESOURCE_KEYS``); nothing of the staff
  who booked, created or changed the booking, its charge item or tags.
- Government organisations (anonymous address picker): government geography only; a team or
  role organisation answers 404.
- The public facility finder (anonymous): without ``cover_image_url``, the duplicate field that
  holds the cover's raw storage key. The key itself stays inside the unsigned
  ``read_cover_image_url`` until P1-T20b signs it.
"""

import datetime

from care.emr.api.otp_viewsets.slot import (
    AppointmentBookingSpec,
    CancelAppointmentSpec,
    OTPSlotViewSet,
)
from care.emr.api.viewsets.facility import (
    AllFacilityViewSet,
    FacilitySchedulableUsersViewSet,
)
from care.emr.api.viewsets.organization import OrganizationPublicViewSet
from care.emr.api.viewsets.scheduling import SlotViewSet
from care.emr.api.viewsets.scheduling.booking import TokenBookingViewSet
from care.emr.models import SchedulableResource
from care.emr.models.patient import Patient
from care.emr.models.scheduling import TokenBooking
from care.emr.resources.base import EMRResource
from care.emr.resources.facility.spec import FacilityMinimalReadSpec
from care.emr.resources.patient.otp_based_flow import PatientOTPReadSpec
from care.emr.resources.scheduling.slot.spec import (
    BookingStatusChoices,
    TokenSlotBaseSpec,
)
from care.users.models import User
from care.utils.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from pydantic import UUID4
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

# What a patient may see of a doctor: what the web app renders (UserCard.tsx, Schedule.tsx,
# formatName). Nothing that reaches the person outside the hospital (phone, username, e-mail).
PUBLIC_PRACTITIONER_KEYS = (
    "id",
    "first_name",
    "last_name",
    "prefix",
    "suffix",
    "profile_picture_url",
)
# A patient's own booking, as the portal shows it (Patient/index.tsx, AppointmentDialog.tsx,
# Success.tsx). Dropped: booked_by, created_by, updated_by (staff records), charge_item (with its
# own staff and invoice) and tags.
BOOKING_KEYS = (
    "id",
    "token_slot",
    "booked_on",
    "status",
    "note",
    "resource_type",
    "resource",
    "facility",
    "patient",
    "created_date",
    "modified_date",
    "token",
)
# A location or healthcare service a booking is for: what the portal shows of it (its name,
# formatScheduleResourceName) and its kind. Never a location's parent copy, current encounter
# (another patient and the care team), tags or organisations; never a service's locations or
# managing organisation. Any other resource type is {} (fail closed).
LOCATION_KEYS = ("id", "name", "description", "location_type", "form", "status", "mode")
HEALTHCARE_SERVICE_KEYS = ("id", "name", "service_type", "internal_type")
RESOURCE_KEYS = {
    "practitioner": PUBLIC_PRACTITIONER_KEYS,
    "location": LOCATION_KEYS,
    "healthcare_service": HEALTHCARE_SERVICE_KEYS,
}
SLOT_KEYS = ("id", "availability", "start_datetime", "end_datetime", "allocated")
AVAILABILITY_KEYS = ("id", "name", "tokens_per_slot", "schedule")
SCHEDULE_KEYS = ("id", "name")
FACILITY_KEYS = ("id", "name")
TOKEN_KEYS = ("number", "status", "category")
TOKEN_CATEGORY_KEYS = ("id", "name", "shorthand")


class PublicPractitionerSpec(EMRResource):
    """One doctor as anyone may see them: ``PUBLIC_PRACTITIONER_KEYS`` and nothing else. Never
    computes the cached roles (CARE's spec saves them on the user from an anonymous GET)."""

    __model__ = User
    __exclude__ = ["geo_organization"]

    id: str | None = None
    first_name: str = ""
    last_name: str = ""
    prefix: str | None = None
    suffix: str | None = None
    profile_picture_url: str | None = None

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        mapping["id"] = str(obj.external_id)
        # the picture's address, never its storage key (the column of the same name)
        mapping["profile_picture_url"] = obj.read_profile_picture_url()

    def to_json(self):
        data = super().to_json()
        return {key: data.get(key) for key in PUBLIC_PRACTITIONER_KEYS}


def public_practitioners(facility_external_id):
    """Staff anyone may see at a facility (list and detail share it, so a doctor hidden from
    the list is a 404 too): a public, not deleted facility; a practitioner with a public, not
    deleted schedule that has not ended (the patient flow books public slots only); active, not
    deleted staff.

    "Not ended" is by India's calendar day, as CARE's slot generator decides it
    (``schedule__valid_to__gte=<day>``): the web app sends ``valid_to`` as a date, stored as
    00:00 IST of the last day, and that day is still bookable (review C1)."""
    resources = SchedulableResource.objects.filter(
        facility__external_id=facility_external_id,
        facility__is_public=True,
        facility__deleted=False,
        resource_type="practitioner",
        deleted=False,
        schedule__is_public=True,
        schedule__deleted=False,
        schedule__valid_to__date__gte=timezone.localdate(),
    ).values("user_id")
    return User.objects.filter(id__in=resources, is_active=True, deleted=False)


class PublicSchedulableUsersViewSet(FacilitySchedulableUsersViewSet):
    pydantic_read_model = PublicPractitionerSpec
    pydantic_retrieve_model = PublicPractitionerSpec

    def get_queryset(self):
        return public_practitioners(self.kwargs["facility_external_id"])


def _pick(data, keys):
    return {key: data.get(key) for key in keys} if isinstance(data, dict) else None


def public_resource(resource):
    """What a booking's patient may see of what it is for, read from the model's own columns
    only (review S1): never CARE's serialiser, which for a location adds its parent's copy
    (saving it when it is cold), the current encounter and tags, and for a doctor the full
    staff record."""
    kind = resource.resource_type
    if kind == "practitioner" and resource.user is not None:
        return PublicPractitionerSpec.serialize(resource.user).to_json()
    if kind == "location" and resource.location is not None:
        location = resource.location
        return {
            "id": str(location.external_id),
            "name": location.name,
            "description": location.description,
            "location_type": location.location_type,
            "form": location.form,
            "status": location.status,
            "mode": location.mode,
        }
    if kind == "healthcare_service" and resource.healthcare_service is not None:
        service = resource.healthcare_service
        return {
            "id": str(service.external_id),
            "name": service.name,
            "service_type": service.service_type,
            "internal_type": service.internal_type,
        }
    return {}


class PublicTokenBookingSpec(EMRResource):
    """A booking as its patient may see it, built from the model (review S1), in place of
    CARE's ``TokenBookingOTPReadSpec``: the same keys as ``BOOKING_KEYS``, and nothing computed
    that reaches staff records, other patients, tags or the charge item."""

    __model__ = TokenBooking
    __exclude__ = [
        "token_slot",
        "patient",
        "token",
        "booked_by",
        "tags",
        "charge_item",
        "associated_encounter",
    ]

    id: UUID4 | None = None
    token_slot: dict = {}
    booked_on: datetime.datetime
    status: str
    note: str | None = None
    resource_type: str
    resource: dict = {}
    facility: dict = {}
    patient: dict = {}
    created_date: datetime.datetime
    modified_date: datetime.datetime
    token: dict | None = None

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        resource = obj.token_slot.resource
        mapping["id"] = obj.external_id
        mapping["token_slot"] = TokenSlotBaseSpec.serialize(obj.token_slot).to_json()
        mapping["resource_type"] = resource.resource_type
        mapping["resource"] = public_resource(resource)
        mapping["facility"] = {
            "id": resource.facility.external_id,
            "name": resource.facility.name,
        }
        mapping["patient"] = PatientOTPReadSpec.serialize(obj.patient).to_json()
        if obj.token:
            category = obj.token.category
            mapping["token"] = {
                "number": obj.token.number,
                "status": obj.token.status,
                "category": {
                    "id": category.external_id,
                    "name": category.name,
                    "shorthand": category.shorthand,
                },
            }


def public_booking(data):
    """The outer allow-list over a booking (every level), kept behind ``PublicTokenBookingSpec``
    so a change to either still shows nothing more."""
    out = {key: data.get(key) for key in BOOKING_KEYS}
    slot = _pick(data.get("token_slot"), SLOT_KEYS)
    if slot is not None:
        availability = _pick(slot["availability"], AVAILABILITY_KEYS)
        if availability is not None:
            availability["schedule"] = _pick(availability["schedule"], SCHEDULE_KEYS)
        slot["availability"] = availability
    out["token_slot"] = slot
    out["resource"] = (
        _pick(data.get("resource"), RESOURCE_KEYS.get(data.get("resource_type"), ()))
        or {}
    )
    out["facility"] = _pick(data.get("facility"), FACILITY_KEYS)
    token = _pick(data.get("token"), TOKEN_KEYS)
    if token is not None:
        token["category"] = _pick(token["category"], TOKEN_CATEGORY_KEYS)
    out["token"] = token
    return out


def booking_json(booking):
    return public_booking(PublicTokenBookingSpec.serialize(booking).to_json())


class PublicOTPSlotViewSet(OTPSlotViewSet):
    """CARE's patient-OTP slot routes with each booking from ``booking_json``. The same routes
    and methods as CARE's actions (same names, so the same URLs and URL names). The bodies are
    CARE's with its booking serialiser replaced; test_public_routes.UpstreamDrift pins CARE's
    three, so a change upstream is carried here before it ships."""

    @extend_schema(request=AppointmentBookingSpec)
    @action(detail=True, methods=["POST"])
    def create_appointment(self, request, *args, **kwargs):
        request_data = AppointmentBookingSpec(**request.data)
        if not Patient.objects.filter(
            external_id=request_data.patient, phone_number=request.user.phone_number
        ).exists():
            raise ValidationError("Patient not allowed")
        appointment = SlotViewSet.create_appointment_handler(
            self.get_object(), request.data, None
        )
        return Response(booking_json(appointment))

    @extend_schema(request=CancelAppointmentSpec)
    @action(detail=False, methods=["POST"])
    def cancel_appointment(self, request, *args, **kwargs):
        request_data = CancelAppointmentSpec(**request.data)
        patient = get_object_or_404(
            Patient,
            external_id=request_data.patient,
            phone_number=request.user.phone_number,
        )
        token_booking = get_object_or_404(
            TokenBooking, external_id=request_data.appointment, patient=patient
        )
        appointment = TokenBookingViewSet.cancel_appointment_handler(
            token_booking, {"reason": BookingStatusChoices.cancelled}, None
        )
        return Response(booking_json(appointment))

    @action(detail=False, methods=["GET"])
    def get_appointments(self, request, *args, **kwargs):
        appointments = TokenBooking.objects.filter(
            patient__phone_number=request.user.phone_number
        )
        return Response({"results": [booking_json(obj) for obj in appointments]})


class GovtOrganizationPublicViewSet(OrganizationPublicViewSet):
    def get_queryset(self):
        return super().get_queryset().filter(org_type="govt")


class PublicFacilityReadSpec(FacilityMinimalReadSpec):
    def to_json(self):
        data = super().to_json()
        # the cover's raw storage key (t/<hospital uuid>/...); read_cover_image_url stays
        data.pop("cover_image_url", None)
        return data


class PublicFacilityViewSet(AllFacilityViewSet):
    pydantic_read_model = PublicFacilityReadSpec
