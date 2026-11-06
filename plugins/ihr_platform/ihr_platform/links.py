"""Links back to a hospital's own web address (TENANCY_DESIGN.md §6.13).

CARE builds password set-up and reset links from one process-wide ``CURRENT_DOMAIN``. In tenant
mode it calls ``settings.FRONTEND_BASE_URL_FUNCTION`` (this function) instead, so each hospital's
staff get links to their own hospital's address.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection


def frontend_base_url():
    from ihr_platform.models import Domain

    schema = getattr(connection, "schema_name", None)
    if not schema or schema == "public":
        msg = (
            "Hospital links can only be built inside a hospital (no tenant is active)."
        )
        raise ImproperlyConfigured(msg)
    domain = (
        Domain.objects.filter(tenant__schema_name=schema)
        .order_by("-is_primary", "id")
        .values_list("domain", flat=True)
        .first()
    )
    if domain is None:
        msg = f"Hospital {schema} has no web address."
        raise ImproperlyConfigured(msg)
    return settings.IHR_FRONTEND_URL_TEMPLATE.format(domain=domain)
