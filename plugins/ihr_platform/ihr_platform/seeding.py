"""Load CARE's demo data into one tenant (tests and benchmarks only; synthetic data).

CARE's fixture script drives the API through a test client. Here that client is pointed at the
tenant's host, so every request is routed to that tenant's schema. Its value sets come from our
reference packs (P1-T15a), never from CARE's sync_valueset.
"""

from contextlib import nullcontext
from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import reverse
from django_tenants.utils import schema_context
from rest_framework.test import APIClient


def load_demo_data(schema_name, domain):
    from care.fixtures import context as fixture_context
    from care.fixtures.context import care_fixture_context
    from care.fixtures.scripts.default_fixtures import load_fixtures
    from ihr_refdata import engine

    from ihr_platform.models import Hospital
    from ihr_platform.provisioning import runner
    from ihr_platform.testing import as_owner

    hospital = Hospital.objects.get(schema_name=schema_name)
    care_call_command = fixture_context.call_command

    def client_for_tenant():
        return APIClient(HTTP_HOST=domain)

    def call_command(name, *args, **kwargs):
        # CARE's fixtures run sync_valueset first: the packs instead (they never insert a
        # duplicate of a soft-deleted slug, nor go around the pack record)
        if name == "sync_valueset":
            # A live hospital can be reached by the start-up apply (ihr_pack_apply --all
            # --startup): take its lock, as every other caller on a live hospital does
            live = hospital.status == Hospital.Status.ACTIVE
            try:
                # Pack apply is an owner-run job (it records the release in public); in tests
                # that run as the app role it runs as the owner, as in production (P1-T19a)
                with (
                    runner.exclusive(hospital.slug) if live else nullcontext(),
                    as_owner(),
                ):
                    engine.apply_packs(hospital, operator="demo-seed")
            except runner.ProvisioningError:
                msg = (
                    f"Another job holds {hospital.slug} (e.g. the start-up pack apply): "
                    "run the seed again when it has finished."
                )
                raise CommandError(msg) from None
            return None
        return care_call_command(name, *args, **kwargs)

    def get_roles(base):
        # CARE's loader reads one page (14) of roles; with ours (P1-T11b) its own Doctor,
        # Nurse, Staff and Administrator would fall off that page (ADR-017)
        data = base.get(reverse("role-list"), params={"limit": 200})
        if data["count"] > len(data["results"]):
            msg = "more roles than one page of the role list holds"
            raise CommandError(msg)
        return {role.name: role for role in data["results"]}

    with (
        schema_context(schema_name),
        override_settings(DEBUG=True),
        patch("care.fixtures.base.CareFixtureBase.get_roles", get_roles),
        patch("care.fixtures.context.APIClient", client_for_tenant),
        patch("care.fixtures.context.call_command", call_command),
        # CARE's demo users are created with passwords, not invited (P1-T5e1)
        patch("ihr_identity.invitations.DEMO_SEEDING", new=True),
        care_fixture_context() as base,
    ):
        load_fixtures(base)


def add_inpatient_stay():
    """Make one demo patient's visit an inpatient stay in a free bed, with its account.

    The Cashier and the TPA Desk see the visit's bed (Q-K37a B) and the account's "Bed charges"
    tab lists it; CARE's own demo data gives visits a random class and no bed, so the web app's
    billing-desk screens (P1-T11c2) had nothing to show. Synthetic data only. Picks the oldest
    account that has a primary visit, turns that visit into an in-progress inpatient visit and
    places it in the first free bed of its hospital (a stay that started two days ago). Run
    inside a hospital's schema; does nothing when that visit already has a bed stay, when no
    account has a visit, or when no bed is free. Returns the visit, or None.

    """
    from datetime import timedelta

    from care.emr.models.account import Account
    from care.emr.models.location import FacilityLocation, FacilityLocationEncounter
    from django.db import transaction
    from django.utils import timezone

    account = (
        Account.objects.filter(primary_encounter__isnull=False, deleted=False)
        .order_by("created_date", "id")
        .select_related("primary_encounter")
        .first()
    )
    if account is None:
        return None
    encounter = account.primary_encounter
    if FacilityLocationEncounter.objects.filter(encounter=encounter).exists():
        return None
    bed = (
        FacilityLocation.objects.filter(
            facility=encounter.facility,
            form="bd",
            mode="instance",
            current_encounter__isnull=True,
            deleted=False,
        )
        .order_by("name", "id")
        .first()
    )
    if bed is None:
        return None
    started = timezone.now() - timedelta(days=2)
    with transaction.atomic():
        encounter.encounter_class = "imp"
        encounter.status = "in_progress"
        encounter.period = {"start": started.isoformat()}
        encounter.current_location = bed
        encounter.save()
        FacilityLocationEncounter.objects.create(
            location=bed, encounter=encounter, status="active", start_datetime=started
        )
        bed.current_encounter = encounter
        bed.system_availability_status = "reserved"
        bed.save()
    return encounter


def complete_demo_contacts():
    """Give CARE's demo patients the permanent address and emergency phone its form would give.

    CARE's own registration form always fills both (it copies the current address and the phone
    unless told otherwise); CARE's demo data leaves them empty. CARE's edit form then refuses to
    save (an empty permanent address), or CARE answers 400 (it sends the empty emergency phone,
    which is not a phone number), so no demo patient could be edited from the web app. The
    front-desk spec edits whichever inpatient it finds first (P1-T11c2 CI, 2026-11-16). Synthetic
    data only; values already set are left alone, so it is safe to run again. Run inside a
    hospital's schema. Returns the number of patients changed.
    """
    from care.emr.models.patient import Patient
    from django.db import transaction
    from django.db.models import F, Q

    with transaction.atomic():
        changed = set(
            Patient.objects.filter(permanent_address="").values_list("pk", flat=True)
        )
        Patient.objects.filter(permanent_address="").update(
            permanent_address=F("address")
        )
        empty_phone = Patient.objects.filter(
            Q(emergency_phone_number="") | Q(emergency_phone_number__isnull=True)
        )
        changed |= set(empty_phone.values_list("pk", flat=True))
        empty_phone.update(emergency_phone_number=F("phone_number"))
    return len(changed)


# The demo hospitals' hospital-wide admin (P1-T24a, Q-K117 A): CARE's fixture user, signed in
# to through the Keycloak demo login admin.priya on alpha (ihr_keycloak_demo_users.DEMO_STAFF).
HOSPITAL_ADMIN = "care-admin"
HOSPITAL_ADMIN_ORGANISATION = "Hospital administration"
ADMIN_ROLE_NAME = "Administrator"  # CARE's system role for a hospital's first admin


def make_hospital_admin(user, facility):
    """Make ``user`` a hospital-wide admin who reaches ``facility``'s menu. Synthetic data only.

    - Hospital-wide (``IsHospitalAdmin``: the audit trail and pack decisions): an admin role
      (a system role holding ``can_create_user``) on an active top-level organisation. CARE's
      fixtures already give care-admin one ("Administrator"); it is kept. Otherwise one is
      added on the first active top-level organisation of type "role" (created as
      "Hospital administration" if the hospital has none). Never a government organisation.
    - The facility: "Facility Admin" at its root organisation, so the facility is in her list;
      the admin pages live under /facility/:id/ (Q-K110 A).

    Safe to run again; returns True when anything was added. Run inside a hospital's schema.
    """
    from care.emr.models.organization import (
        FacilityOrganizationUser,
        Organization,
        OrganizationUser,
    )
    from care.security.models import RoleModel
    from ihr_identity.staff_access import hospital_wide_admin

    added = False
    if not hospital_wide_admin(user):
        role = RoleModel.objects.filter(name=ADMIN_ROLE_NAME, is_system=True).first()
        if role is None:
            message = f"The system role {ADMIN_ROLE_NAME!r} is missing: run sync_permissions_roles."
            raise RuntimeError(message)
        organization = (
            Organization.objects.filter(
                parent__isnull=True, active=True, deleted=False, org_type="role"
            )
            .order_by("id")
            .first()
        ) or Organization.objects.create(
            name=HOSPITAL_ADMIN_ORGANISATION, org_type="role"
        )
        OrganizationUser.objects.create(organization=organization, user=user, role=role)
        added = True
    _, created = FacilityOrganizationUser.objects.get_or_create(
        organization=facility.default_internal_organization,
        user=user,
        defaults={"role": RoleModel.objects.get(name="Facility Admin", is_system=True)},
    )
    return added or created
