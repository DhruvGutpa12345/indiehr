"""Load CARE's demo data into one tenant (tests and benchmarks only; synthetic data).

CARE's fixture script drives the API through a test client. Here that client is pointed at the
tenant's host, so every request is routed to that tenant's schema.
"""

from unittest.mock import patch

from django.test import override_settings
from django_tenants.utils import schema_context
from rest_framework.test import APIClient


def load_demo_data(schema_name, domain):
    from care.fixtures.context import care_fixture_context
    from care.fixtures.scripts.default_fixtures import load_fixtures

    def client_for_tenant():
        return APIClient(HTTP_HOST=domain)

    with (
        schema_context(schema_name),
        override_settings(DEBUG=True),
        patch("care.fixtures.context.APIClient", client_for_tenant),
        care_fixture_context() as base,
    ):
        load_fixtures(base)
