"""Bring a new hospital live from the command line (P1-T5a; the console in P1-T5b runs the same).

    manage.py ihr_provision start --slug gamma --name "Gamma Health Pvt Ltd" \\
        --display-name "Gamma Hospital" --state-code 27 --district Pune --city Pune \\
        --pin-code 411001 --phone +912000000003 --address "1 Test Road" \\
        --facility-name "Gamma Hospital" --admin-email admin@gamma.example.test \\
        --admin-first-name Gita --admin-last-name Rao [--stop-after STEP]
    manage.py ihr_provision status [OP_ID | --slug gamma]
    manage.py ihr_provision resume OP_ID
    manage.py ihr_provision rollback OP_ID
    manage.py ihr_provision resend-setup OP_ID     # the first admin's setup e-mail, again
    manage.py ihr_provision dev-activate-admin --slug gamma      # development only

Keycloak steps need IHR_KEYCLOAK_ADMIN_CLIENT_ID/_SECRET in this process's environment
(scripts/care-dev.sh passes them); the web app and the worker never hold them.
"""

import getpass
import json
import secrets

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ihr_platform.keycloak.kc_http import KeycloakError
from ihr_platform.models import Hospital, TenantOperation
from ihr_platform.provisioning import runner, steps

FORM_FIELDS = [f.replace("_", "-") for f in steps.OnboardingForm.__dataclass_fields__]


class Command(BaseCommand):
    help = "Provision a new hospital, resumably (TENANCY_DESIGN §7.1)."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        start = sub.add_parser("start")
        for field in FORM_FIELDS:
            start.add_argument(f"--{field}", required=field != "facility-type")
        start.add_argument("--stop-after", choices=steps.STEP_NAMES)
        status = sub.add_parser("status")
        status.add_argument("op_id", nargs="?")
        status.add_argument("--slug")
        resume = sub.add_parser("resume")
        resume.add_argument("op_id")
        resume.add_argument("--stop-after", choices=steps.STEP_NAMES)
        rollback = sub.add_parser("rollback")
        rollback.add_argument("op_id")
        resend = sub.add_parser("resend-setup")
        resend.add_argument("op_id")
        dev = sub.add_parser("dev-activate-admin")
        dev.add_argument("--slug", required=True)

    def handle(self, *args, action, **options):
        try:
            getattr(self, "do_" + action.replace("-", "_"))(**options)
        except (runner.ProvisioningError, steps.StepError, KeycloakError) as exc:
            raise CommandError(str(exc)) from exc

    def _requested_by(self):
        return f"cli:{getpass.getuser()}"

    def _op(self, op_id=None, slug=None):
        ops = TenantOperation.objects.filter(kind=TenantOperation.Kind.PROVISION)
        op = ops.filter(pk=op_id).first() if op_id else None
        if op is None and slug:
            op = ops.filter(slug=slug).order_by("-created_at").first()
        if op is None:
            raise CommandError("No such provisioning operation.")
        return op

    def _report(self, op):
        total = 0.0
        self.stdout.write(f"{op.slug}: {op.status}  (operation {op.id})")
        for record in op.steps:
            seconds = record.get("seconds")
            total += seconds or 0
            line = f"  {record['name']:<14} {record['status']:<10}"
            if seconds is not None:
                line += f" {seconds:>7.1f} s"
            if record.get("error"):
                line += f"  {record['error']}"
            self.stdout.write(line)
        self.stdout.write(f"  {'total':<14} {'':<10} {total:>7.1f} s")

    def _run(self, op, stop_after):
        try:
            runner.run(op, stop_after=stop_after)
        except (runner.ProvisioningError, steps.StepError):
            raise
        except Exception:
            op.refresh_from_db()
            self._report(op)
            msg = f"Stopped with an error. Fix it, then: ihr_provision resume {op.id}"
            raise CommandError(msg) from None
        op.refresh_from_db()
        self._report(op)

    def do_start(self, stop_after=None, **options):
        values = {
            f.replace("-", "_"): options[f.replace("-", "_")]
            for f in FORM_FIELDS
            if options.get(f.replace("-", "_")) is not None
        }
        form = steps.OnboardingForm(**values)
        op = runner.start(form, self._requested_by())
        self._run(op, stop_after)

    def do_status(self, op_id=None, slug=None, **options):
        self._report(self._op(op_id, slug))

    def do_resume(self, op_id, stop_after=None, **options):
        self._run(self._op(op_id), stop_after)

    def do_rollback(self, op_id, **options):
        op = runner.rollback(self._op(op_id), self._requested_by())
        self.stdout.write(f"{op.slug}: rolled back; its address can be used again.")

    def do_resend_setup(self, op_id, **options):
        op = runner.resend_setup(self._op(op_id))
        self.stdout.write(
            f"{op.slug}: setup e-mail sent again to {op.input['admin_email']}."
        )

    def do_dev_activate_admin(self, slug, **options):
        """Development only: replace the first admin's e-mailed setup (P1-T5c) with a
        ready-made dev login, for scripted demos. Prints one JSON line for care-dev.sh."""
        if not settings.DEBUG or settings.IHR_TENANT_DOMAIN_SUFFIX != "localhost":
            raise CommandError("Development only.")
        from django_tenants.utils import schema_context
        from ihr_identity.models import StaffIdentity

        from ihr_platform.keycloak import totp
        from ihr_platform.keycloak.hospital import ensure_member, hospital_ids
        from ihr_platform.keycloak.users import create_user
        from ihr_platform.management.commands.ihr_keycloak_sync import keycloak_admin

        hospital = Hospital.objects.filter(slug=slug, status="active").first()
        op = TenantOperation.objects.filter(
            kind="provision", slug=slug, status="succeeded"
        ).first()
        if hospital is None or op is None:
            msg = f"{slug} has not been provisioned."
            raise CommandError(msg)
        form = op.input
        username = f"admin.{slug}"[:30]
        password = f"Ihr-{secrets.token_urlsafe(12)}-7"
        otp_secret = totp.new_secret()
        admin = keycloak_admin()
        with schema_context(hospital.schema_name):
            # Re-running replaces this dev login (and only this one, found by its link).
            for link in StaffIdentity.objects.filter(
                user_id=op.result["admin_user_id"]
            ):
                admin.call("DELETE", f"users/{link.keycloak_sub}", ok=(204, 404))
                link.delete()
        sub = create_user(
            admin,
            username,
            form["admin_first_name"],
            form["admin_last_name"],
            password,
            otp_secret,
            replace=False,
        )
        ensure_member(
            admin, hospital_ids(admin, slug, str(hospital.keycloak_org_id)), sub
        )
        with schema_context(hospital.schema_name):
            from care.users.models import User

            user = User.objects.get(pk=op.result["admin_user_id"])
            StaffIdentity.objects.create(user=user, keycloak_sub=sub)
            User.objects.filter(pk=user.pk).update(is_active=True)
        self.stdout.write(
            json.dumps(
                {
                    "username": username,
                    "name": f"{form['admin_first_name']} {form['admin_last_name']}",
                    "password": password,
                    "otp_secret": otp_secret,
                    "hospitals": [slug],
                    "care_users": {slug: user.username},
                }
            )
        )
