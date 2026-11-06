"""Object-storage key prefix per tenant (TENANCY_DESIGN.md §6.8).

CARE calls this through ``settings.FILE_OBJECT_KEY_PREFIX_FUNCTION`` for every file and image key.
The prefix uses the tenant's immutable UUID, never its schema name or slug.
"""

from django.core.exceptions import ImproperlyConfigured
from django.db import connection

_uuid_by_schema = {}


def tenant_key_prefix():
    schema = getattr(connection, "schema_name", None)
    if not schema or schema == "public":
        msg = "Files can only be stored inside a tenant (no tenant is active)."
        raise ImproperlyConfigured(msg)
    if schema not in _uuid_by_schema:
        from ihr_platform.models import Hospital

        _uuid_by_schema[schema] = str(
            Hospital.objects.values_list("uuid", flat=True).get(schema_name=schema)
        )
    return f"t/{_uuid_by_schema[schema]}/"
