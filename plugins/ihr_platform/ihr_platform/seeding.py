"""Load CARE's demo data into one tenant (tests and benchmarks only; synthetic data).

CARE's fixture script drives the API through a test client. Here that client is pointed at the
tenant's host, so every request is routed to that tenant's schema. Its value sets come from our
reference packs (P1-T15a), never from CARE's sync_valueset.
"""

from contextlib import nullcontext
from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import override_settings
from django_tenants.utils import schema_context
from rest_framework.test import APIClient


def load_demo_data(schema_name, domain):
    from care.fixtures import context as fixture_context
    from care.fixtures.context import care_fixture_context
    from care.fixtures.scripts.default_fixtures import load_fixtures
    from ihr_refdata import engine

    from ihr_platform.models import Hospital
    from ihr_platform.provisioning import runner

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
                with runner.exclusive(hospital.slug) if live else nullcontext():
                    engine.apply_packs(hospital, operator="demo-seed")
            except runner.ProvisioningError:
                msg = (
                    f"Another job holds {hospital.slug} (e.g. the start-up pack apply): "
                    "run the seed again when it has finished."
                )
                raise CommandError(msg) from None
            return None
        return care_call_command(name, *args, **kwargs)

    with (
        schema_context(schema_name),
        override_settings(DEBUG=True),
        patch("care.fixtures.context.APIClient", client_for_tenant),
        patch("care.fixtures.context.call_command", call_command),
        # CARE's demo users are created with passwords, not invited (P1-T5e1)
        patch("ihr_identity.invitations.DEMO_SEEDING", new=True),
        care_fixture_context() as base,
    ):
        load_fixtures(base)
