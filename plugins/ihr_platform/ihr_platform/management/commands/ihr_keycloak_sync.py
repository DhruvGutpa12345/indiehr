"""Bring Keycloak in line with our hospitals (P1-T3, ADR-008).

    manage.py ihr_keycloak_sync --realm --all        # dev: create/repair realm, every hospital
    manage.py ihr_keycloak_sync --all --check        # report drift, change nothing (exit 1 if any)
    manage.py ihr_keycloak_sync --slug alpha --rebuild-flows

The admin credential comes from this process's environment only
(IHR_KEYCLOAK_ADMIN_CLIENT_ID / _SECRET, a service account in the ``master`` realm, dev and CI
only); it is never a Django setting, so the web app never holds it. Production onboarding gets a
narrower credential in P1-T5.
"""

import os
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

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


def keycloak_admin():
    try:
        client_id = os.environ["IHR_KEYCLOAK_ADMIN_CLIENT_ID"]
        secret = os.environ["IHR_KEYCLOAK_ADMIN_CLIENT_SECRET"]
        base_url = settings.IHR_KEYCLOAK_URL
    except (KeyError, AttributeError) as exc:
        msg = (
            f"Keycloak is not configured ({exc}): set IHR_KEYCLOAK_URL in settings and "
            "IHR_KEYCLOAK_ADMIN_CLIENT_ID/_SECRET in the environment."
        )
        raise CommandError(msg) from exc
    return KeycloakAdmin(base_url, client_id, secret, settings.IHR_KEYCLOAK_REALM)


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
    if settings.IHR_KEYCLOAK_DEV_REDIRECTS:
        if settings.IHR_TENANT_DOMAIN_SUFFIX != "localhost" or not settings.DEBUG:
            msg = "IHR_KEYCLOAK_DEV_REDIRECTS is for local development only."
            raise CommandError(msg)
        dev = f"http://{hospital.slug}.localhost:8082"
        origins.append(dev)
        redirects.append(f"{dev}/")
    return HospitalLogin(
        slug=hospital.slug,
        name=hospital.name,
        origins=tuple(origins),
        redirect_uris=tuple(redirects),
        enabled=hospital.status not in INACTIVE,
        uuid=str(hospital.uuid),
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
        issues = []
        try:
            if realm and not check:
                for item in ensure_realm(admin):
                    self.stdout.write(f"fixed: {item}")
            if realm or check:
                # With --all every hospital client is known, so leftovers are reported too.
                known = {client_id_for(h.slug) for h in hospitals} if every else None
                issues += check_realm(admin, known)
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
