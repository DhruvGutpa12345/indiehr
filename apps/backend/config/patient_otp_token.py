from datetime import timedelta

from django.db import connection
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import Token


class PatientToken(Token):
    lifetime = timedelta(hours=1)
    token_type = "patient_login"

    # IndieHR (TENANCY_DESIGN.md §4.7): under schema-per-tenant, a token is bound to the tenant
    # schema that issued it, so it cannot be replayed at another hospital with the same phone.
    # Without django-tenants (no ``schema_name`` on the connection) behaviour is unchanged.
    def __init__(self, token=None, verify=True):
        super().__init__(token, verify)
        schema = getattr(connection, "schema_name", None)
        if token is None and schema:
            self["tid"] = schema

    def verify(self):
        super().verify()
        schema = getattr(connection, "schema_name", None)
        if schema and self.payload.get("tid") != schema:
            msg = "Token is not valid for this tenant"
            raise TokenError(msg)
