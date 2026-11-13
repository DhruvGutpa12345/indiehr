from django.apps import AppConfig
from django.db.backends.signals import connection_created
from django.db.models.signals import post_migrate, pre_migrate


class IhrAuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ihr_audit"
    verbose_name = "IndieHR audit trail"

    def ready(self):
        from celery.signals import task_postrun, task_prerun
        from rest_framework.views import APIView

        from ihr_audit import context, reads

        post_migrate.connect(_install, sender=self, dispatch_uid="ihr_audit_install")
        # A database-role refusal found by _install is raised by the last receiver, so the
        # other apps' post_migrate work (content types, permissions) runs first (P1-T19a, C6)
        from django.apps import apps
        from ihr_platform import db_roles

        last = [c for c in apps.get_app_configs() if c.models_module is not None][-1]
        post_migrate.connect(
            db_roles.raise_deferred, sender=last, dispatch_uid="ihr_db_roles_deferred"
        )
        # ... and each migrate run starts with nothing deferred (second review of C6)
        pre_migrate.connect(
            db_roles.clear_deferred, weak=False, dispatch_uid="ihr_db_roles_clear"
        )
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
    from ihr_platform import db_roles

    from ihr_audit.install import ensure_triggers
    from ihr_audit.seal import ensure_sealing

    connection = connections[using]
    schema = getattr(connection, "schema_name", "public")
    if schema != "public":
        ensure_triggers(connection, schema)
        # The sealing functions and the stamp trigger, this build's (C5), before the grants
        ensure_sealing(connection, schema)
    # The app role's grants, beside the triggers (P1-T19a): this schema's tables as the
    # reviewed lists say, and the database itself after the public schema's run. Off: nothing
    db_roles.repair_after_migrate(connection, schema)
