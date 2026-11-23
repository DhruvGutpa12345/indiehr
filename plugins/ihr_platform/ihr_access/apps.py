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

        from ihr_access import (
            actions,
            authz,
            exceptions,
            guard,
            identifiers,
            records,
            referrals,
            state,
            visits,
        )
        from ihr_access import checks as access_checks

        # The serializer guard on every patient, encounter, bed, device, booking and token shape
        guard.install()
        # PatientUser rows follow the person's current job; the hidden role needs a live grant
        authz.install()
        # A placement refusal answers 400 with its reason (review C3)
        exceptions.install()
        # Search and identifier writes keep to the identifier allow-list (review S2)
        identifiers.install()
        # A visit named in the query must be the path patient's (P1-T11b3 review K1)
        visits.install()
        # CARE's action engine runs nothing for our roles, and the referrals assigned to one
        # of them are not listed (P2-T0a, review F2)
        actions.install()
        referrals.install()
        # A records grant opens its patient's requests, specimens, reports and prescriptions
        # (reads only), before CARE's controller builds its lazy map; and file links issued
        # under a grant live at most 5 minutes (P1-T11b4)
        authz.install_grant_authorizer()
        records.install()
        checks.register(access_checks.guard_installed)
        checks.register(access_checks.filters_classified)
        checks.register(access_checks.records_grants)

        # Each DRF view starts with a fresh state for the person it authenticated (after the
        # audit actor is bound: ihr_audit wraps first); each request starts and ends with none
        authenticate = APIView.perform_authentication

        def perform_authentication(view, request):
            authenticate(view, request)
            state.begin(request.user)
            # Expired records grants end now; live ones mark the request (P1-T11b4)
            records.on_request(request.user)

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
