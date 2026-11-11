"""This process's Keycloak client key, checked against the real Keycloak (P1-T8d2, ADR-012).

    manage.py ihr_client_keys status                  # the key folder: kids, states, ages
    manage.py ihr_client_keys probe [--kid KID]       # does Keycloak accept our signature?
    manage.py ihr_client_keys probe --negative        # ...and refuse every bad one?
    manage.py ihr_client_keys revoke-sessions         # end every staff and operator session

``status`` and ``probe`` run inside the key holder's own container (``compose exec``): the
backend, the provisioner or the staff worker. The backend's probe asks for a token with a junk
refresh token, as the console and as one live hospital: ``invalid_grant`` means Keycloak
accepted our signature and only the token was wrong, ``invalid_client`` that it refused us. A
worker's probe asks for a real client-credentials token.

``probe`` exits 0 when Keycloak accepted the signature for every client, 1 when it refused it for
every client, 3 when it accepted some and refused others, and 2 when the probe itself failed.
``probe --negative`` exits 0 only if Keycloak refused every bad assertion.

``revoke-sessions`` (owner, Q-K20: after a suspected leak of the backend's key) needs the
bootstrap credential, like ihr_keycloak_sync. Nothing here prints a key or an assertion.
"""

import secrets
import time
from datetime import UTC, datetime
from http import HTTPStatus

import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from ihr_identity import client_auth, oidc

from ihr_platform import client_keys
from ihr_platform.keycloak.accounts import PURPOSES
from ihr_platform.keycloak.hospital import client_id_for
from ihr_platform.keycloak.kc_http import KeycloakError
from ihr_platform.keycloak.realm import end_all_sessions

ACCEPTED, REFUSED, MIXED, FAILED = 0, 1, 3, 2
# What Keycloak answers when it refused the client's authentication.
CLIENT_ERRORS = {"invalid_client", "unauthorized_client"}
TIMEOUT = 10


class ProbeError(Exception):
    """The probe could not tell (Keycloak unreachable, an unexpected answer)."""


def _failed(msg):
    return CommandError(msg, returncode=FAILED)


class Command(BaseCommand):
    help = "Check this process's Keycloak client key, or end every session."

    def add_arguments(self, parser):
        commands = parser.add_subparsers(dest="command", required=True)
        commands.add_parser("status", help="the key folder: kids, states, ages")
        probe = commands.add_parser("probe", help="does Keycloak accept our key?")
        probe.add_argument("--kid", help="sign with this key, not the active one")
        probe.add_argument(
            "--negative",
            action="store_true",
            help="check that Keycloak refuses every kind of bad assertion",
        )
        commands.add_parser(
            "revoke-sessions",
            help="end every staff and operator session (bootstrap credential)",
        )

    def handle(self, *args, command, **options):
        if command == "revoke-sessions":
            return self.revoke_sessions()
        store = client_auth.store()
        try:
            purpose = store.purpose()
            if purpose is None:
                msg = f"no client key in {settings.IHR_CLIENT_KEY_DIR}"
                raise client_keys.ClientKeyError(msg)
            if command == "status":
                return self.status(store)
            store.check()
        except client_keys.ClientKeyError as exc:
            raise _failed(str(exc)) from exc
        targets = self.targets(purpose)
        if options["negative"]:
            return self.negative(store, purpose, targets[0])
        return self.probe(store, purpose, targets, options["kid"])

    # --- status ---------------------------------------------------------------------------

    def status(self, store):
        status = store.status()
        for key in status["keys"]:
            self.stdout.write(
                f"{key['kid']}  {key['state']:<8}  created {key['created_ist']}  "
                f"{key['age_days']} days"
            )
        self.stdout.write(
            f"{status['verdict']} client key {status['purpose']}: {status['message']}"
        )
        if status["verdict"] == "FAIL":
            raise CommandError(status["message"], returncode=1)

    # --- probe ----------------------------------------------------------------------------

    def targets(self, purpose):
        """(client id, realm) pairs this key signs for, as Keycloak should know them."""
        if purpose != "backend":
            return [(PURPOSES[purpose], settings.IHR_KEYCLOAK_REALM)]
        from ihr_platform.models import Hospital

        found = []
        if getattr(settings, "IHR_CONSOLE_URL", ""):
            found.append((settings.IHR_CONSOLE_CLIENT, settings.IHR_OPS_REALM))
        hospital = (
            Hospital.objects.filter(
                status=Hospital.Status.ACTIVE, keycloak_org_id__isnull=False
            )
            .exclude(schema_name="public")
            .order_by("slug")
            .first()
        )
        if hospital is not None:
            found.append((client_id_for(hospital.slug), settings.IHR_KEYCLOAK_REALM))
        if not found:
            msg = "nothing to probe: no console and no live hospital"
            raise _failed(msg)
        return found

    def _ask(self, purpose, client_id, realm, fields):
        """Keycloak's verdict on one request: "accepted" or "refused"."""
        if purpose == "backend":
            # A junk token: client authentication comes first, so invalid_grant means it passed.
            grant = {
                "grant_type": "refresh_token",
                "refresh_token": "probe.not.a.token",
            }
        else:
            grant = {"grant_type": "client_credentials"}
        try:
            resp = requests.post(
                oidc._backchannel("token", realm),  # noqa: SLF001
                data={**grant, "client_id": client_id, **fields},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            msg = f"Keycloak unreachable: {type(exc).__name__}"
            raise ProbeError(msg) from exc
        try:
            error = (resp.json() or {}).get("error", "")
        except ValueError:
            error = ""
        # The body is Keycloak's answer; a worker's access token in it is never shown.
        if (
            purpose != "backend"
            and resp.status_code == HTTPStatus.OK
            and "access_token" in resp.text
        ):
            return "accepted"
        if purpose == "backend" and error == "invalid_grant":
            return "accepted"
        if error in CLIENT_ERRORS:
            return "refused"
        msg = (
            f"unexpected answer from Keycloak: HTTP {resp.status_code} {error}".rstrip()
        )
        raise ProbeError(msg)

    def probe(self, store, purpose, targets, kid):
        kid = kid or store.active_kid()
        results = []
        for client_id, realm in targets:
            audience = oidc.issuer(realm)
            try:
                fields = store.fields(client_id, audience, kid)
                verdict = self._ask(purpose, client_id, realm, fields)
            except (client_keys.ClientKeyError, ProbeError) as exc:
                msg = f"{client_id}: {exc}"
                raise _failed(msg) from exc
            self.stdout.write(f"{verdict:<8}  {client_id} ({realm}), key {kid}")
            results.append(verdict)
        if set(results) == {"accepted"}:
            return
        if set(results) == {"refused"}:
            msg = f"Keycloak refused key {kid}"
            raise CommandError(msg, returncode=REFUSED)
        msg = f"Keycloak accepted key {kid} for some clients only (sync --check)"
        raise CommandError(msg, returncode=MIXED)

    def negative(self, store, purpose, target):
        """Every kind of bad assertion must be refused with invalid_client."""
        client_id, realm = target
        issuer = oidc.issuer(realm)
        kid = store.active_kid()
        key = store.private_key(kid)
        stranger = client_keys.new_private_key()
        now = int(time.time())

        def signed(signing_key=key, **kw):
            options = {"audience": issuer, "kid": kid} | kw
            return client_keys.assertion_fields(
                client_keys.make_assertion(
                    signing_key,
                    options.pop("kid"),
                    client_id,
                    options.pop("audience"),
                    **options,
                )
            )

        cases = [
            ("no assertion", {}),
            ("a client secret", {"client_secret": secrets.token_urlsafe(32)}),
            ("another key under the active kid", signed(stranger)),
            ("an unknown kid", signed(stranger, kid=f"{purpose}-unknownunknown00")),
            ("an expired assertion", signed(now=now - 120)),
            (
                # Keycloak bounds the age (now - iat <= max.exp), not exp - iat.
                f"issued over {client_keys.MAX_LIFETIME} s ago, not yet expired",
                signed(now=now - 90, lifetime=120),
            ),
            (
                "two audiences",
                signed(audience=[issuer, f"{issuer}/protocol/openid-connect/token"]),
            ),
            (
                "HS256 instead of ES256",
                signed(secrets.token_bytes(32), algorithm="HS256"),
            ),
            (
                "RS256 instead of ES256",
                signed(
                    rsa.generate_private_key(public_exponent=65537, key_size=2048),
                    algorithm="RS256",
                ),
            ),
        ]
        internal = f"{settings.IHR_KEYCLOAK_URL}/realms/{realm}"
        if internal != issuer:
            # Keycloak checks the audience against its public name only.
            cases.append(
                ("the internal address as audience", signed(audience=internal))
            )
        else:
            self.stdout.write(
                "skipped   internal audience: the same as the public issuer"
            )
        failures = 0
        try:
            for label, fields in cases:
                verdict = self._ask(purpose, client_id, realm, fields)
                failures += self._report(label, verdict, "refused")
            replay = signed()
            first = self._ask(purpose, client_id, realm, replay)
            if first != "accepted":
                self.stdout.write(
                    "FAIL      a good assertion was refused: is the key published?"
                )
                failures += 1
            else:
                verdict = self._ask(purpose, client_id, realm, replay)
                failures += self._report("the same assertion twice", verdict, "refused")
        except ProbeError as exc:
            msg = f"{client_id}: {exc}"
            raise _failed(msg) from exc
        if failures:
            msg = f"Keycloak accepted {failures} bad assertion(s) for {client_id}"
            raise CommandError(msg, returncode=1)
        self.stdout.write(f"every bad assertion refused for {client_id} ({realm})")

    def _report(self, label, verdict, want):
        ok = verdict == want
        self.stdout.write(f"{'ok' if ok else 'FAIL':<8}  {verdict}: {label}")
        return 0 if ok else 1

    # --- after a suspected leak -----------------------------------------------------------

    def revoke_sessions(self):
        from ihr_platform.management.commands.ihr_keycloak_sync import keycloak_admin

        realms = [settings.IHR_KEYCLOAK_REALM]
        if getattr(settings, "IHR_CONSOLE_URL", ""):
            realms.append(settings.IHR_OPS_REALM)
        for realm in realms:
            try:
                not_before = end_all_sessions(keycloak_admin(realm))
            except KeycloakError as exc:
                raise CommandError(str(exc)) from exc
            when = datetime.fromtimestamp(not_before, UTC).astimezone(client_keys.IST)
            self.stdout.write(
                f"{realm}: every session ended; tokens from before "
                f"{when:%d-%m-%Y %H:%M:%S} IST are refused"
            )
