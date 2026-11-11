"""Bring Keycloak in line with our hospitals (P1-T3, ADR-008).

    manage.py ihr_keycloak_sync --realm --all        # dev: create/repair realm, every hospital
    manage.py ihr_keycloak_sync --all --check        # report drift, change nothing (exit 1 if any)
    manage.py ihr_keycloak_sync --slug alpha --rebuild-flows

The admin credential comes from this process's environment only
(IHR_KEYCLOAK_ADMIN_CLIENT_ID / _SECRET, a service account in the ``master`` realm); it is never
a Django setting, so the web app never holds it. The workers use their own narrow accounts and
sign with their own keys instead (P1-T5f, P1-T8d2).

The public key sets written into Keycloak (every client authenticates with a signed assertion,
P1-T8d2) come from IHR_CLIENT_JWKS_DIR: ``backend``, ``onboarding`` and ``staff``.jwks.json,
written by the key tool. Each client's keys are drift-checked against them.
"""

import os
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from ihr_identity import client_auth

from ihr_platform.keycloak.accounts import (
    PURPOSES,
    check_admin_holders,
    check_service_accounts,
    ensure_service_accounts,
)
from ihr_platform.keycloak.hospital import (
    HospitalLogin,
    check_hospital,
    client_id_for,
    ensure_hospital,
    rebuild_flow,
)
from ihr_platform.keycloak.kc_http import KeycloakAdmin, KeycloakError
from ihr_platform.keycloak.realm import check_realm, ensure_realm
from ihr_platform.models import Domain, Hospital

INACTIVE = {
    Hospital.Status.PROVISIONING,  # turned on by provisioning's last step (P1-T5a)
    Hospital.Status.SUSPENDED,
    Hospital.Status.OFFBOARDING,
    Hospital.Status.ARCHIVED,
}


def keycloak_admin(realm=None, purpose=None):
    """A Keycloak admin client.

    ``purpose`` ("onboarding" or "staff", P1-T5f): the worker's own narrow service account of
    the hospitals' realm, signing with the worker's own key (IHR_CLIENT_KEY_DIR, P1-T8d2).
    Without ``purpose``, or in a process that does not hold that worker's key (an operator's
    command line), the operator's bootstrap credential (IHR_KEYCLOAK_ADMIN_CLIENT_ID/_SECRET).
    The workers' containers hold only their own key, never the bootstrap credential."""
    base_url = getattr(settings, "IHR_KEYCLOAK_URL", None)
    if not base_url:
        raise CommandError("Keycloak is not configured: set IHR_KEYCLOAK_URL.")
    realm = realm or settings.IHR_KEYCLOAK_REALM
    if purpose is not None:
        store = client_auth.store()
        try:
            held = store.purpose()
        except client_auth.ClientKeyError as exc:
            raise CommandError(str(exc)) from exc
        if held == purpose:
            if realm != settings.IHR_KEYCLOAK_REALM:
                msg = f"The {purpose} account works in {settings.IHR_KEYCLOAK_REALM} only."
                raise CommandError(msg)
            client_id = PURPOSES[purpose]
            audience = f"{settings.IHR_KEYCLOAK_PUBLIC_URL}/realms/{realm}"
            return KeycloakAdmin(
                base_url,
                client_id,
                None,
                realm,
                account_realm=realm,
                client_auth=lambda: store.fields(client_id, audience),
            )
    try:
        client_id = os.environ["IHR_KEYCLOAK_ADMIN_CLIENT_ID"]
        secret = os.environ["IHR_KEYCLOAK_ADMIN_CLIENT_SECRET"]
    except KeyError as exc:
        wanted = (
            f"the {purpose} key in {settings.IHR_CLIENT_KEY_DIR}" if purpose else ""
        )
        msg = (
            f"No Keycloak credential in this process ({wanted or exc}): workers sign "
            "with their own key; operators use the bootstrap credential."
        )
        raise CommandError(msg) from exc
    return KeycloakAdmin(base_url, client_id, secret, realm)


def public_jwks(purpose):
    """The published key set of ``purpose``; a missing or malformed set stops the command."""
    try:
        return client_auth.public_jwks(purpose)
    except client_auth.ClientKeyError as exc:
        raise CommandError(str(exc)) from exc


def worker_jwks():
    """Each worker account's key set, for ensure/check_service_accounts."""
    return {client: public_jwks(purpose) for purpose, client in PURPOSES.items()}


def mail_server():
    """Keycloak's mail server for setup e-mails (P1-T5c). Outside development it must use
    TLS: the e-mails carry one-time setup links. Its password, like the admin credential,
    comes from this process's environment (IHR_KEYCLOAK_SMTP_PASSWORD), never a setting
    that every web and worker process would load."""
    smtp = dict(settings.IHR_KEYCLOAK_SMTP or {})
    if not smtp:
        return None
    if "password" in smtp:
        msg = (
            "Put the mail-server password in IHR_KEYCLOAK_SMTP_PASSWORD, not settings."
        )
        raise CommandError(msg)
    if not settings.DEBUG:
        tls = {str(smtp.get(k, "")).lower() for k in ("ssl", "starttls")}
        if "true" not in tls:
            msg = (
                "IHR_KEYCLOAK_SMTP must use TLS (ssl or starttls) outside development."
            )
            raise CommandError(msg)
    if os.environ.get("IHR_KEYCLOAK_SMTP_PASSWORD"):
        smtp.update(auth="true", password=os.environ["IHR_KEYCLOAK_SMTP_PASSWORD"])
    return smtp


def hospital_login(hospital):
    """The hospital's sign-in addresses, from its own web addresses."""
    domains = list(
        Domain.objects.filter(tenant=hospital)
        .order_by("-is_primary", "id")
        .values_list("domain", flat=True)
    )
    if not domains:
        msg = f"{hospital.slug} has no web address."
        raise CommandError(msg)
    origins = [settings.IHR_FRONTEND_URL_TEMPLATE.format(domain=d) for d in domains]
    # The backend finishes the sign-in (P1-T4, ADR-009).
    redirects = [f"{o}/api/ihr/auth/callback/" for o in origins]
    # The backend's public keys (P1-T8d2). Only processes that write the client have them
    # (the sync and the provisioner); elsewhere, e.g. the staff worker reading a sign-in
    # address, the set stays empty, and ensure_hospital refuses an empty set.
    try:
        jwks = client_auth.public_jwks("backend")
    except client_auth.ClientKeyError:
        jwks = ""
    return HospitalLogin(
        slug=hospital.slug,
        name=hospital.name,
        origins=tuple(origins),
        redirect_uris=tuple(redirects),
        backchannel_url=settings.IHR_BACKCHANNEL_LOGOUT_URL,
        enabled=hospital.status not in INACTIVE,
        uuid=str(hospital.uuid),
        jwks=jwks,
    )


class Command(BaseCommand):
    help = (
        "Create or check the Keycloak realm and each hospital's organisation and login."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--realm", action="store_true", help="Also the realm baseline"
        )
        parser.add_argument("--slug", action="append", default=[])
        parser.add_argument(
            "--all", dest="every", action="store_true", help="Every hospital"
        )
        parser.add_argument("--check", action="store_true", help="Report drift only")
        parser.add_argument("--rebuild-flows", action="store_true")

    def _ensure_realm(self, admin):
        keys = worker_jwks()  # before any change: a missing set stops the sync here
        for item in ensure_realm(admin, smtp=mail_server()):
            self.stdout.write(f"fixed: {item}")
        for item in check_service_accounts(admin, keys):
            self.stdout.write(f"fixed: {item}")
        # The workers' narrow accounts (P1-T5f), with their keys (P1-T8d2)
        ensure_service_accounts(admin, keys)

    def handle(self, *args, realm, slug, every, check, rebuild_flows, **options):
        admin = keycloak_admin()
        # Hospitals still being provisioned belong to the provisioning engine (P1-T5a).
        hospitals = (
            Hospital.objects.exclude(schema_name="public")
            .exclude(status=Hospital.Status.PROVISIONING)
            .order_by("slug")
        )
        if not every:
            hospitals = hospitals.filter(slug__in=slug)
            missing = set(slug) - set(hospitals.values_list("slug", flat=True))
            if missing:
                msg = f"No hospital {sorted(missing)}"
                raise CommandError(msg)
        if not check and hospitals.exists():
            public_jwks("backend")  # stop before any change rather than per hospital
        issues = []
        try:
            if realm and not check:
                self._ensure_realm(admin)
                if getattr(settings, "IHR_CONSOLE_URL", ""):
                    self._ensure_operator_realm()
            if realm or check:
                # With --all every hospital client is known, so leftovers are reported too.
                known = {client_id_for(h.slug) for h in hospitals} if every else None
                issues += check_realm(admin, known, smtp=mail_server())
                issues += self._check_accounts(admin)
            for hospital in hospitals:
                try:
                    issues += self._sync_one(admin, hospital, check, rebuild_flows)
                except (KeycloakError, CommandError) as exc:
                    # One broken hospital must not stop the others from being synced.
                    issues.append(f"{hospital.slug}: {exc}")
        except KeycloakError as exc:
            raise CommandError(str(exc)) from exc
        for item in issues:
            self.stderr.write(f"DRIFT {item}")
        if issues:
            msg = f"{len(issues)} problem(s) found in Keycloak"
            raise CommandError(msg)
        self.stdout.write(self.style.SUCCESS("Keycloak matches the baseline."))

    def _check_accounts(self, admin):
        """The workers' accounts and the console client, keys included. A missing key set
        is reported like any other difference."""
        issues = []
        try:
            keys = worker_jwks()
        except CommandError as exc:
            issues.append(str(exc))
            keys = None
        issues += check_service_accounts(admin, keys)
        issues += check_admin_holders(admin)
        if getattr(settings, "IHR_CONSOLE_URL", ""):
            from ihr_platform.console.auth import console_url, redirect_uri
            from ihr_platform.keycloak.ops import check_ops_realm

            try:
                backend_keys = public_jwks("backend")
            except CommandError as exc:
                issues.append(str(exc))
                backend_keys = ""
            issues += check_ops_realm(
                keycloak_admin(settings.IHR_OPS_REALM),
                redirect_uri(),
                console_url(),
                backend_keys,
            )
        return issues

    def _ensure_operator_realm(self):
        from ihr_platform.console.auth import console_url, redirect_uri
        from ihr_platform.keycloak.ops import ensure_ops_realm

        ops = keycloak_admin(settings.IHR_OPS_REALM)
        ensure_ops_realm(ops, redirect_uri(), console_url(), public_jwks("backend"))
        self.stdout.write(f"operator realm {settings.IHR_OPS_REALM}: console ready")

    def _sync_one(self, admin, hospital, check, rebuild_flows):
        login = hospital_login(hospital)
        org_id = str(hospital.keycloak_org_id) if hospital.keycloak_org_id else None
        if check:
            if org_id is None:
                return [f"{hospital.slug}: not linked to Keycloak yet"]
            return check_hospital(admin, login, org_id)
        sync = rebuild_flow if rebuild_flows else ensure_hospital
        ids = sync(admin, login, org_id)
        if org_id is None:
            hospital.keycloak_org_id = uuid.UUID(ids.org_id)
            hospital.save(update_fields=["keycloak_org_id"])
        self.stdout.write(
            f"{hospital.slug}: {ids.client_id} -> organisation {ids.org_id}"
        )
        return check_hospital(admin, login, ids.org_id)
