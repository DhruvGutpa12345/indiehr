"""Show or update a hospital's profile and branding (platform operators; the console comes in P1-T5).

    manage.py ihr_hospital_profile --slug alpha
    manage.py ihr_hospital_profile --slug alpha --display-name "Alpha Hospital" \\
        --gstin 27AAPFU0939F1ZV --state-code 27 --pin-code 411001 --logo /path/logo.png
"""

import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context
from ihr_platform import release
from ihr_platform.models import Hospital

from ihr_tenant_settings.models import HospitalProfile

TEXT_FIELDS = (
    "display_name",
    "short_name",
    "legal_name",
    "gstin",
    "address_line1",
    "address_line2",
    "city",
    "district",
    "state_code",
    "pin_code",
    "phone",
    "email",
    "website",
    "clinical_establishment_reg_no",
    "nabh_accreditation_no",
    "hfr_id",
    "rohini_id",
    "letterhead_footer",
    "primary_colour",
)


class Command(BaseCommand):
    help = "Show or update a hospital's profile and branding."

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True, help="The hospital's slug")
        for field in TEXT_FIELDS:
            parser.add_argument(f"--{field.replace('_', '-')}", dest=field)
        parser.add_argument("--logo", help="PNG, JPEG or WebP file, at most 256 KB")
        parser.add_argument("--remove-logo", action="store_true")

    def handle(self, *args, slug, logo, remove_logo, **options):
        hospital = (
            Hospital.objects.filter(slug=slug).exclude(schema_name="public").first()
        )
        if hospital is None:
            msg = f"No hospital with slug {slug!r}"
            raise CommandError(msg)
        release.require_ready(hospital)  # never into a held or older schema (S6)
        with schema_context(hospital.schema_name):
            profile = HospitalProfile.current()
            for field in TEXT_FIELDS:
                if options.get(field) is not None:
                    value = options[field]
                    setattr(
                        profile, field, value.upper() if field == "gstin" else value
                    )
            if logo:
                profile.logo = Path(logo).read_bytes()
            if remove_logo:
                profile.logo = None
            try:
                profile.save()
            except ValidationError as error:
                msg = f"Not saved: {error.message_dict}"
                raise CommandError(msg) from error
            shown = {field: getattr(profile, field) for field in TEXT_FIELDS}
            shown["logo"] = profile.logo_content_type or None
        self.stdout.write(json.dumps(shown, indent=2, ensure_ascii=False))
