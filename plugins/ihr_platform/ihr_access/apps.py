from django.apps import AppConfig
from django.core import checks
from django.core.signals import request_finished, request_started
from django.db.models.signals import post_migrate


class IhrAccessConfig(AppConfig):
    """Demographics-and-billing access for the restricted roles (P1-T11b; design-b.md).

    A tenant app: it loads after CARE's apps (ihr_platform/settings/tenancy.py), so importing
    CARE's specs and authorizers here is safe. It must stay a tenant app (design-b.md B15)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "ihr_access"
    verbose_name = "IndieHR access for restricted roles"

    def ready(self):
        from rest_framework.views import APIView

        from ihr_access import authz, exceptions, guard, identifiers, state
        from ihr_access import checks as access_checks

        # The serializer guard on every patient, encounter, bed, device, booking and token shape
        guard.install()
        # PatientUser rows follow the person's current job; the hidden role needs a live grant
        authz.install()
        # A placement refusal answers 400 with its reason (review C3)
        exceptions.install()
        # Search and identifier writes keep to the identifier allow-list (review S2)
        identifiers.install()
        checks.register(access_checks.guard_installed)

        # Each DRF view starts with a fresh state for the person it authenticated (after the
        # audit actor is bound: ihr_audit wraps first); each request starts and ends with none
        authenticate = APIView.perform_authentication

        def perform_authentication(view, request):
            authenticate(view, request)
            state.begin(request.user)

        APIView.perform_authentication = perform_authentication
        request_started.connect(state.end, weak=False, dispatch_uid="ihr_access_start")
        request_finished.connect(
            state.end, weak=False, dispatch_uid="ihr_access_finish"
        )

        post_migrate.connect(_install, sender=self, dispatch_uid="ihr_access_placement")


def _install(using="default", **kwargs):
    from django.db import connections

    from ihr_access import placement

    connection = connections[using]
    schema = getattr(connection, "schema_name", "public")
    if schema != "public":
        placement.ensure(connection, schema)
