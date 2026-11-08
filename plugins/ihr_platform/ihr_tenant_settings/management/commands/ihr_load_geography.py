"""Add every state and district in the PIN directory to hospitals' geography (P1-T7a).

    manage.py ihr_load_geography --slug alpha
    manage.py ihr_load_geography --all

Safe to repeat: existing states and districts are kept, only missing ones are added.
Only live (active) hospitals: one being provisioned gets them when it goes live, and a
suspended, offboarding or archived hospital is left as it is.
"""

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context
from ihr_platform import pincodes
from ihr_platform.models import Hospital

from ihr_tenant_settings import geography


class Command(BaseCommand):
    help = "Load states and districts from the PIN directory into hospitals."

    def add_arguments(self, parser):
        which = parser.add_mutually_exclusive_group(required=True)
        which.add_argument("--slug", help="One hospital")
        which.add_argument("--all", action="store_true", help="Every hospital")

    def handle(self, *args, slug, all, **options):  # noqa: A002 (the option's name)
        if pincodes.current() is None:
            msg = "No PIN directory loaded: run ihr_load_pincodes first."
            raise CommandError(msg)
        hospitals = Hospital.objects.exclude(schema_name="public").order_by("slug")
        if slug:
            hospitals = hospitals.filter(slug=slug)
            hospital = hospitals.first()
            if hospital is None:
                msg = f"No hospital with slug {slug!r}"
                raise CommandError(msg)
            if hospital.status != Hospital.Status.ACTIVE:
                msg = f"{slug} is {hospital.status}: only live hospitals are changed."
                raise CommandError(msg)
        hospitals = hospitals.filter(status=Hospital.Status.ACTIVE)
        for hospital in hospitals:
            with schema_context(hospital.schema_name):
                states, added = geography.load_all()
            self.stdout.write(
                f"{hospital.slug}: {states} states, {added} districts added"
            )
