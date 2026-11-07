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
        from ihr_tenant_settings.india import GST_STATE_CODES

        problems = self._address_problems()
        if self.state_code not in GST_STATE_CODES:
            problems.append("state_code: not an Indian GST state code")
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
    # Only on a schema this operation created: on an existing one it would skip or, with
    # --overwrite, wipe the hospital's own edits (TENANCY_DESIGN §7.2).
    if op.result.get("schema_created_here"):
        with schema_context(_hospital(op).schema_name):
            call_command("sync_valueset", verbosity=0)


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
    """The hospital's state and district as CARE government organisations (the start of our
    own state loader, TENANCY_DESIGN §5)."""
    from care.emr.models.organization import Organization
    from ihr_tenant_settings.india import GST_STATE_CODES

    # The same shape as CARE's own loader (load_govt_organization_csv), so a later full
    # geography load finds these rows instead of adding duplicates.
    state_meta = {
        "country": "India",
        "govt_org_type": "state",
        "govt_org_children_type": "district",
    }
    district_meta = {
        "country": "India",
        "govt_org_type": "district",
        "govt_org_children_type": "local_body",
    }
    with schema_context(_hospital(op).schema_name), transaction.atomic():
        state, _ = Organization.objects.filter(
            name__iexact=GST_STATE_CODES[form.state_code],
            org_type="govt",
            parent=None,
            metadata=state_meta,
        ).get_or_create(
            defaults={
                "name": GST_STATE_CODES[form.state_code],
                "org_type": "govt",
                "system_generated": True,
                "metadata": state_meta,
            }
        )
        district, _ = Organization.objects.filter(
            name__iexact=form.district,
            parent=state,
            org_type="govt",
            metadata=district_meta,
        ).get_or_create(
            defaults={
                "name": form.district,
                "org_type": "govt",
                "parent": state,
                "system_generated": True,
                "metadata": district_meta,
            }
        )
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
    ids = ensure_hospital(keycloak_admin(), hospital_login(hospital), stored)
    if stored is None:
        Hospital.objects.filter(pk=hospital.pk).update(keycloak_org_id=ids.org_id)
    op.result["keycloak_org_id"] = ids.org_id


def activate(op, form):
    """Live: the sign-in client turned on, then status ACTIVE and the go-live time (once)."""
    from ihr_platform.keycloak.hospital import ensure_hospital
    from ihr_platform.management.commands.ihr_keycloak_sync import (
        hospital_login,
        keycloak_admin,
    )

    hospital = _hospital(op)
    login = replace(hospital_login(hospital), enabled=True)
    ensure_hospital(keycloak_admin(), login, str(hospital.keycloak_org_id))
    Hospital.objects.filter(pk=hospital.pk, activated_at__isnull=True).update(
        activated_at=timezone.now()
    )
    Hospital.objects.filter(pk=hospital.pk).update(status=Hospital.Status.ACTIVE)


STEPS = [
    ("reserve", reserve),
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
]
STEP_NAMES = [name for name, _ in STEPS]
