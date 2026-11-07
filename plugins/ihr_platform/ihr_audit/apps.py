from django.apps import AppConfig
from django.db.backends.signals import connection_created
from django.db.models.signals import post_migrate


class IhrAuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ihr_audit"
    verbose_name = "IndieHR audit trail"

    def ready(self):
        from celery.signals import task_postrun, task_prerun
        from rest_framework.views import APIView

        from ihr_audit import context, reads

        post_migrate.connect(_install, sender=self, dispatch_uid="ihr_audit_install")
        reads.connect()

        # Every DRF authentication class, ours and CARE's patient-OTP ones, ends here.
        authenticate = APIView.perform_authentication

        def perform_authentication(view, request):
            authenticate(view, request)
            context.bind_actor(request.user)

        APIView.perform_authentication = perform_authentication

        task_prerun.connect(
            lambda task=None, **kw: context.begin_task(task),
            weak=False,
            dispatch_uid="ihr_audit_task_start",
        )
        task_postrun.connect(
            lambda **kw: context.end_task(),
            weak=False,
            dispatch_uid="ihr_audit_task_end",
        )
        connection_created.connect(
            context.reapply, weak=False, dispatch_uid="ihr_audit_reapply"
        )


def _install(using="default", **kwargs):
    from django.db import connections

    from ihr_audit.install import ensure_triggers

    connection = connections[using]
    schema = getattr(connection, "schema_name", "public")
    if schema != "public":
        ensure_triggers(connection, schema)
