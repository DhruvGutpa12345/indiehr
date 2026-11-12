"""The steps that bring a new hospital live (P1-T5a, TENANCY_DESIGN §7.1).

Each step is idempotent: run again, it finds its own earlier work (by the ids it recorded in
``op.result``) and changes nothing. Steps never save the Hospital model (a save could create
the schema by itself, django-tenants' ``auto_create_schema``); they update rows directly.
"""

import re
from dataclasses import asdict, dataclass, replace

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone
from django_tenants.utils import schema_context, schema_exists

from ihr_platform.models import SLUG_RE, Domain, Hospital

# The "starter data" the owner chose (2026-11-07): CARE's roles and code lists, the
# hospital profile, its state and district, one facility and the first admin.
DEFAULT_FACILITY_TYPE = "Private Hospital"
PIN_RE = re.compile(r"^[1-9][0-9]{5}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)


# Field lengths of where each value is stored (HospitalProfile, Facility, User).
MAX_LENGTHS = {
    "name": 200,
    "display_name": 255,
    "district": 100,
    "city": 100,
    "address": 200,
    "facility_name": 255,
    "admin_first_name": 150,
    "admin_last_name": 150,
}


class StepError(Exception):
    """A step refused to continue; the message is safe to show an operator."""


@dataclass
class OnboardingForm:
    slug: str
    name: str  # legal entity (GSTIN holder)
    display_name: str
    state_code: str
    district: str
    city: str
    pin_code: str
    phone: str
    address: str
    facility_name: str
    admin_email: str
    admin_first_name: str
    admin_last_name: str
    facility_type: str = DEFAULT_FACILITY_TYPE

    def validate(self):
        from care.facility.models.facility import REVERSE_REVERSE_FACILITY_TYPES
        from care.utils.models.validators import mobile_or_landline_number_validator
        from ihr_tenant_settings.geography import district_problem
        from ihr_tenant_settings.india import GST_STATE_CODES

        problems = self._address_problems()
        if self.state_code not in GST_STATE_CODES:
            problems.append("state_code: not an Indian GST state code")
        elif problem := district_problem(self.state_code, self.district):
            problems.append(f"district: {problem}")
        if not PIN_RE.match(self.pin_code):
            problems.append("pin_code: six digits, not starting with 0")
        if not EMAIL_RE.match(self.admin_email):
            problems.append("admin_email: not an e-mail address")
        if self.facility_type not in REVERSE_REVERSE_FACILITY_TYPES:
            problems.append("facility_type: not one of CARE's facility types")
        try:
            mobile_or_landline_number_validator(self.phone)
        except ValidationError:
            problems.append("phone: not an Indian phone number (+91…)")
        for field, limit in MAX_LENGTHS.items():
            value = str(getattr(self, field)).strip()
            if not value:
                problems.append(f"{field}: required")
            elif len(value) > limit:
                problems.append(f"{field}: at most {limit} characters")
        if problems:
            raise StepError("; ".join(problems))

    def _address_problems(self):
        from ihr_platform.models import RESERVED_SLUGS, RetiredAddress

        address = f"{self.slug}.{settings.IHR_TENANT_DOMAIN_SUFFIX}"
        if not SLUG_RE.match(self.slug) or self.slug in RESERVED_SLUGS:
            return ["slug: 3-31 lower-case letters, digits or hyphens; not reserved"]
        if RetiredAddress.objects.filter(kind="slug", value=self.slug).exists():
            return ["slug: belonged to another hospital and is never reused"]
        if Hospital.objects.filter(slug=self.slug).exists():
            return ["slug: already taken"]
        if (
            Domain.objects.filter(domain=address).exists()
            or RetiredAddress.objects.filter(kind="domain", value=address).exists()
        ):
            return ["slug: its web address is taken or retired"]
        return []

    def as_dict(self):
        return asdict(self)


def _hospital(op):
    hospital = Hospital.objects.filter(pk=op.hospital_id).first()
    if hospital is None or str(hospital.uuid) != op.result.get("hospital_uuid"):
        raise StepError("the hospital of this operation is missing or was replaced")
    return hospital


def _keycloak_admin():
    from ihr_platform.management.commands.ihr_keycloak_sync import keycloak_admin

    return keycloak_admin(purpose="onboarding")


# --- steps -----------------------------------------------------------------------------


def reserve(op, form):
    """The Hospital row and its empty schema, recorded on the operation in one transaction:
    the slug is taken, nothing is reachable yet (no address), and no hospital ever exists
    without a schema (the start-up ``migrate`` walks every hospital)."""
    if op.hospital_id:
        return
    form.validate()
    with transaction.atomic():
        hospital = Hospital(slug=form.slug, name=form.name)
        hospital.auto_create_schema = False  # migrated by the schema step, resumably
        hospital.save()
        created = not schema_exists(hospital.schema_name)
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{hospital.schema_name}"')
        op.hospital = hospital
        op.result.update(
            hospital_uuid=str(hospital.uuid),
            schema_name=hospital.schema_name,
            schema_created_here=created,
        )
        op.save(update_fields=["hospital", "result"])


def admin_account(op, form):
    """The first admin's Keycloak account (P1-T5c): new, stamped with this hospital, with no
    password or authenticator, so nobody can sign in with it until the person sets it up
    from their e-mail. An address that already has an account stops the run here, before
    anything else is built (joining an existing account comes with P1-T5e)."""
    from ihr_platform.keycloak.invite import ExistingAccountError, ensure_invited_user

    try:
        sub = ensure_invited_user(
            _keycloak_admin(),
            form.admin_email,
            form.admin_first_name,
            form.admin_last_name,
            op.result["hospital_uuid"],
            op.result.get("admin_keycloak_sub"),
        )
    except ExistingAccountError as exc:
        raise StepError(str(exc)) from None
    op.result["admin_keycloak_sub"] = sub
    op.save(update_fields=["result"])


def schema(op, form):
    """Run every migration in the hospital's schema; a resume finishes the migrations."""
    hospital = _hospital(op)
    call_command(
        "migrate_schemas",
        tenant=True,
        schema_name=hospital.schema_name,
        interactive=False,
        verbosity=0,
    )


def domains(op, form):
    """The hospital's web address, only once its schema is complete."""
    hospital = _hospital(op)
    domain, _ = Domain.objects.get_or_create(
        domain=address_of(hospital.slug),
        defaults={"tenant": hospital, "is_primary": True},
    )
    if domain.tenant_id != hospital.pk:
        raise StepError("the address belongs to another hospital")


def address_of(slug):
    return f"{slug}.{settings.IHR_TENANT_DOMAIN_SUFFIX}"


def roles(op, form):
    with schema_context(_hospital(op).schema_name):
        call_command("sync_permissions_roles", verbosity=0)


def value_sets(op, form):
    """The reference packs (P1-T15a), CARE's system value sets first. The engine never
    overwrites a hospital's own edits and writes nothing when nothing changed, so it is safe
    on any schema; this run already holds the hospital's lock."""
    from ihr_refdata import engine

    engine.apply_packs(_hospital(op), operator=_pack_operator(op))


def _pack_operator(op):
    return f"provisioning:{op.requested_by}"[:150]


def profile(op, form):
    from ihr_tenant_settings.models import HospitalProfile

    with schema_context(_hospital(op).schema_name):
        current = HospitalProfile.current()
        current.display_name = form.display_name
        current.short_name = form.display_name[:40]
        current.legal_name = form.name
        current.city = form.city
        current.district = form.district
        current.state_code = form.state_code
        current.pin_code = form.pin_code
        current.phone = form.phone
        current.address_line1 = form.address
        current.save()


def geography(op, form):
    """The hospital's state and district as CARE government organisations, plus every
    other state and district in the PIN directory once it is loaded (P1-T7a)."""
    from ihr_tenant_settings import geography as geo

    with schema_context(_hospital(op).schema_name), transaction.atomic():
        state = geo.ensure_state(form.state_code)
        district, _ = geo.ensure_district(
            state, geo.directory_district(form.state_code, form.district)
        )
        geo.load_all()
        op.result["state_org_id"] = state.id
        op.result["district_org_id"] = district.id
        op.save(update_fields=["result"])  # committed together with the rows


def first_admin(op, form):
    """The hospital's first administrator as an *inactive* CARE user with no usable
    password: they become active only by accepting their invitation (P1-T5c). Created
    directly, so CARE's own "set your password" e-mail is never sent."""
    from care.emr.models.organization import Organization, OrganizationUser
    from care.security.models import RoleModel
    from care.users.models import User
    from care.utils.models.validators import UsernameValidator
    from django.core.validators import validate_email

    with schema_context(_hospital(op).schema_name), transaction.atomic():
        user = User.objects.filter(pk=op.result.get("admin_user_id")).first()
        if user is None:
            user = User(
                username=_free_username(User, form.admin_email),
                email=form.admin_email.lower(),
                first_name=form.admin_first_name,
                last_name=form.admin_last_name,
                user_type="administrator",
                is_active=False,
                geo_organization_id=op.result["district_org_id"],
            )
            user.set_unusable_password()
            # CARE's own rules for these two fields (its other fields are for clinicians).
            UsernameValidator()(user.username)
            validate_email(user.email)
            user.save()
            op.result["admin_user_id"] = user.id
            op.save(update_fields=["result"])  # committed together with the user
        state = Organization.objects.get(pk=op.result["state_org_id"])
        OrganizationUser.objects.get_or_create(
            organization=state,
            user=user,
            defaults={"role": RoleModel.objects.get(name="Administrator")},
        )


def _free_username(user_model, email):
    """A username CARE accepts (4-16 of a-z 0-9 . _ -, no doubled separators)."""
    base = re.sub(r"[^a-z0-9]+", ".", email.split("@")[0].lower()).strip(".")
    base = (base or "admin")[:12].strip(".") or "admin"
    if len(base) < 4:  # noqa: PLR2004
        base = f"{base}.adm"
    candidate, n = base, 1
    while user_model.objects.filter(username=candidate).exists():
        n += 1
        candidate = f"{base}{n}"
    return candidate


def facility(op, form):
    """One facility; CARE adds its root organisation and makes the first admin its
    Facility Admin (``Facility.save``)."""
    from care.facility.models.facility import (
        REVERSE_REVERSE_FACILITY_TYPES,
        Facility,
    )

    with schema_context(_hospital(op).schema_name), transaction.atomic():
        existing = Facility.objects.filter(pk=op.result.get("facility_id")).first()
        if existing is not None:
            return
        found = Facility.objects.filter(name__iexact=form.facility_name).first()
        if found is not None:
            raise StepError("a facility with this name exists and is not ours")
        created = Facility.objects.create(
            name=form.facility_name,
            facility_type=REVERSE_REVERSE_FACILITY_TYPES[form.facility_type],
            address=form.address,
            pincode=int(form.pin_code),
            phone_number=form.phone,
            geo_organization_id=op.result["district_org_id"],
            created_by_id=op.result["admin_user_id"],
        )
        op.result["facility_id"] = created.id
        op.save(update_fields=["result"])  # committed together with the facility


def keycloak(op, form):
    """The hospital's Keycloak organisation, sign-in client and gate (ADR-008). The client
    stays disabled until the hospital goes live."""
    from ihr_platform.keycloak.hospital import ensure_hospital
    from ihr_platform.management.commands.ihr_keycloak_sync import (
        hospital_login,
        keycloak_admin,
    )

    hospital = _hospital(op)
    stored = str(hospital.keycloak_org_id) if hospital.keycloak_org_id else None
    ids = ensure_hospital(
        keycloak_admin(purpose="onboarding"), hospital_login(hospital), stored
    )
    if stored is None:
        Hospital.objects.filter(pk=hospital.pk).update(keycloak_org_id=ids.org_id)
    op.result["keycloak_org_id"] = ids.org_id


def activate(op, form):
    """Live: the sign-in client turned on, then status ACTIVE and the go-live time (once)."""
    from ihr_refdata import engine
    from ihr_tenant_settings import geography as geo

    from ihr_platform.keycloak.hospital import ensure_hospital
    from ihr_platform.management.commands.ihr_keycloak_sync import (
        hospital_login,
        keycloak_admin,
    )

    hospital = _hospital(op)
    # ihr_load_geography skips hospitals being provisioned: a directory loaded after the
    # geography step (a resumed run skips that step) is picked up here, before going live.
    with schema_context(hospital.schema_name):
        geo.load_all()
    # Likewise ihr_pack_apply --all: a run resumed under a newer release must not go live
    # with an older pack (P1-T15a)
    engine.apply_packs(hospital, operator=_pack_operator(op))
    login = replace(hospital_login(hospital), enabled=True)
    ensure_hospital(
        keycloak_admin(purpose="onboarding"), login, str(hospital.keycloak_org_id)
    )
    Hospital.objects.filter(pk=hospital.pk, activated_at__isnull=True).update(
        activated_at=timezone.now()
    )
    Hospital.objects.filter(pk=hospital.pk).update(status=Hospital.Status.ACTIVE)


def invite(op, form):
    """Link the first admin's account here and send their one-time setup e-mail (P1-T5c).
    Their account here stays inactive until they first sign in with the password and
    authenticator they chose (ihr_identity.views). Runs once the hospital is live: the link
    leads back to its (until then disabled) sign-in."""
    from ihr_platform.keycloak.invite import account_state

    hospital = _hospital(op)
    admin = _keycloak_admin()
    _link_first_admin(op, hospital, admin, op.result["admin_keycloak_sub"])
    if op.result.get("setup_email_sent_at") or send_setup(op, hospital):
        return
    # Sent before a crash that lost the record, and already used: nothing left to do.
    if (
        account_state(admin, op.result["admin_keycloak_sub"], str(hospital.uuid))
        != "set_up"
    ):
        raise StepError("the first admin's Keycloak account is missing")


def _link_first_admin(op, hospital, admin, sub):
    from ihr_identity.models import StaffIdentity

    from ihr_platform.keycloak.hospital import ensure_member, hospital_ids

    ensure_member(
        admin, hospital_ids(admin, hospital.slug, str(hospital.keycloak_org_id)), sub
    )
    with schema_context(hospital.schema_name):
        identity, _ = StaffIdentity.objects.get_or_create(
            user_id=op.result["admin_user_id"],
            defaults={"keycloak_sub": sub, "awaiting_setup": True},
        )
    if str(identity.keycloak_sub) != sub:
        raise StepError("the first admin is linked to another account")


def send_setup(op, hospital=None):
    """E-mail the first admin a one-time setup link. False if there is nothing to send:
    their account is gone or already set up."""
    from ihr_platform.keycloak.hospital import client_id_for
    from ihr_platform.keycloak.invite import send_setup_email
    from ihr_platform.management.commands.ihr_keycloak_sync import hospital_login

    sub = op.result.get("admin_keycloak_sub")
    if not sub:  # a hospital provisioned before P1-T5c
        return False
    hospital = hospital or _hospital(op)
    hours = settings.IHR_SETUP_LINK_HOURS
    sent = send_setup_email(
        _keycloak_admin(),
        sub,
        str(hospital.uuid),
        client_id_for(hospital.slug),
        # "Back to application" after setting up: our callback sends them to sign in.
        hospital_login(hospital).redirect_uris[0],
        hours * 3600,
    )
    if sent:
        op.result.update(
            setup_email_sent_at=timezone.now().isoformat(), setup_link_hours=hours
        )
        op.save(update_fields=["result"])
    return sent


def resend_setup(op):
    """A fresh setup link for a first admin who has not set up yet. Keycloak cannot cancel
    a link it has sent, so the unfinished account is replaced by a new one: every earlier
    link names the old account and stops working. False if there is nothing to resend
    (set up, or the hospital was made before P1-T5c)."""
    from ihr_identity.models import StaffIdentity

    from ihr_platform.keycloak.invite import (
        account_state,
        delete_pending,
        ensure_invited_user,
    )

    old = op.result.get("admin_keycloak_sub")
    if not old:
        return False
    hospital = _hospital(op)
    admin = _keycloak_admin()
    tag = str(hospital.uuid)
    with schema_context(hospital.schema_name):
        waiting = StaffIdentity.objects.filter(
            user_id=op.result["admin_user_id"], awaiting_setup=True
        ).exists()
    if not waiting or account_state(admin, old, tag) == "set_up":
        return False
    if not delete_pending(admin, old, tag):
        raise StepError("the first admin's account could not be replaced")
    form = OnboardingForm(**op.input)
    # Steps below are each safe to repeat: a crash part-way is fixed by sending again.
    sub = ensure_invited_user(
        admin, form.admin_email, form.admin_first_name, form.admin_last_name, tag
    )
    op.result["admin_keycloak_sub"] = sub
    op.result.pop("setup_email_sent_at", None)
    op.save(update_fields=["result"])
    with schema_context(hospital.schema_name):
        StaffIdentity.objects.filter(
            user_id=op.result["admin_user_id"], awaiting_setup=True
        ).update(keycloak_sub=sub)
    _link_first_admin(op, hospital, admin, sub)
    return send_setup(op, hospital)


STEPS = [
    ("reserve", reserve),
    ("admin_account", admin_account),
    ("schema", schema),
    ("domains", domains),
    ("roles", roles),
    ("value_sets", value_sets),
    ("profile", profile),
    ("geography", geography),
    ("first_admin", first_admin),
    ("facility", facility),
    ("keycloak", keycloak),
    ("activate", activate),
    ("invite", invite),
]
STEP_NAMES = [name for name, _ in STEPS]
