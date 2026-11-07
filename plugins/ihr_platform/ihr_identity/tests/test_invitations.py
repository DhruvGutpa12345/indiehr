"""Staff invitations (P1-T5e1): CARE's "Add user" invites, the provisioner delivers, and an
existing account joins only through the e-mailed link, a sign-in and the "Join" click.

Keycloak is faked (ihr_identity.testing); keycloak/tests/test_live.py covers the real calls and
the dev click-through covers the whole path.
"""

import re
import time
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from care.users.models import User
from django.conf import settings
from django.core import mail
from django.test import SimpleTestCase
from django.utils import timezone
from django_tenants.utils import schema_context
from ihr_platform import invitations
from ihr_platform.keycloak.invite import ExistingAccountError
from ihr_platform.models import Hospital, StaffInvitation
from ihr_platform.test_runner import TEST_SCHEMA
from rest_framework.test import APIClient

from ihr_identity import oidc, sessions, testing
from ihr_identity.models import StaffIdentity

HOST = "testserver"
USERS = "/api/v1/users/"


def hospital():
    with schema_context("public"):
        return testing.ensure_org_id(Hospital.objects.get(schema_name=TEST_SCHEMA))


def new_user_payload(**changes):
    stamp = str(time.time_ns())[-8:]
    return {
        "username": f"inv{stamp}",
        "email": f"New.Person{stamp}@Example.TEST",
        "first_name": "New",
        "last_name": "Person",
        "phone_number": f"+9198{stamp}",
        "gender": "female",
        "role_orgs": [],
    } | changes


class FakeKeycloak:
    """The Keycloak calls the provisioner makes for an invitation, with account states:
    created here (pending until ``set_up``), existing (set up), or made by another hospital
    and never set up (``elsewhere``)."""

    PENDING = ["VERIFY_EMAIL", "UPDATE_PASSWORD", "CONFIGURE_TOTP"]

    def __init__(self):
        self.existing, self.elsewhere = {}, {}
        self.created, self.members, self.setup_mails = {}, [], []
        self.set_up = set()

    def account(self, sub):
        pending = sub not in self.set_up and (
            sub in self.created.values() or sub in self.elsewhere.values()
        )
        stamp = {"ihr_invited_for": ["other"]} if sub in self.elsewhere.values() else {}
        return {
            "id": sub,
            "attributes": stamp,
            "requiredActions": self.PENDING if pending else [],
        }

    def ensure_invited_user(self, admin, email, first, last, tag, sub=None):
        if email in self.existing or email in self.elsewhere:
            raise ExistingAccountError("exists")
        return self.created.setdefault(email, str(uuid.uuid4()))

    def find_invited(self, admin, sub, tag):
        return self.account(sub) if sub in self.created.values() else None

    def existing_account(self, admin, email):
        return self.existing.get(email) or self.elsewhere[email]

    def ensure_member(self, admin, ids, sub):
        self.members.append((ids.slug, sub))

    def send_setup_email(self, admin, sub, tag, client_id, uri, seconds):
        self.setup_mails.append((sub, client_id, uri, seconds))
        return True

    def patches(self):
        kc = self
        admin = SimpleNamespace(get=lambda path: kc.account(path.split("/")[1]))
        targets = {
            "ihr_platform.keycloak.invite.ensure_invited_user": self.ensure_invited_user,
            "ihr_platform.keycloak.invite.find_invited": self.find_invited,
            "ihr_platform.keycloak.invite.send_setup_email": self.send_setup_email,
            "ihr_platform.keycloak.hospital.ensure_member": self.ensure_member,
            "ihr_platform.keycloak.hospital.hospital_ids": lambda a, slug, org: (
                SimpleNamespace(slug=slug)
            ),
            "ihr_platform.invitations._existing_account": self.existing_account,
            "ihr_platform.management.commands.ihr_keycloak_sync.keycloak_admin": (
                lambda: admin
            ),
        }
        return [mock.patch(t, v) for t, v in targets.items()]


class InvitationCase(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        self.hospital = hospital()
        name = f"adm-{time.time_ns()}"[:16]
        self.admin = User.objects.create_superuser(
            username=name, email=f"{name}@example.test", password="x-Unused-1"
        )
        self.api = APIClient(HTTP_HOST=HOST)
        self.api.force_authenticate(
            self.admin
        )  # CARE's own permission checks still run
        self.keycloak = testing.fake_keycloak()
        self.keycloak.__enter__()
        self.kc = FakeKeycloak()
        mail.outbox = []

    def tearDown(self):
        self.keycloak.__exit__(None, None, None)
        with schema_context("public"):
            StaffInvitation.objects.filter(hospital=self.hospital).delete()
        made = User.objects.filter(username__startswith="inv")
        StaffIdentity.objects.filter(user__in=made).delete()
        made.delete()
        self.admin.delete()

    def invite(self, **changes):
        response = self.api.post(USERS, new_user_payload(**changes), format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        user = User.objects.get(username=response.json()["username"])
        with schema_context("public"):
            return user, StaffInvitation.objects.get(
                hospital=self.hospital, care_user_id=user.pk
            )

    def deliver(self, existing=()):
        for address in existing:  # the same account (sub) every time it is asked about
            self.kc.existing.setdefault(address.lower(), str(uuid.uuid4()))
            self.kc.set_up.add(self.kc.existing[address.lower()])
        patches = self.kc.patches()
        for patch in patches:
            patch.start()
        try:
            with schema_context("public"):
                while invitations.deliver_once():
                    pass
        finally:
            for patch in patches:
                patch.stop()


class InvitationTests(InvitationCase):
    # --- "Add user" ------------------------------------------------------------------

    def test_add_user_creates_an_inactive_user_and_queues_an_invitation(self):
        user, inv = self.invite()
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(mail.outbox, [])  # none of CARE's own e-mails
        self.assertEqual(inv.status, "queued")
        self.assertEqual(inv.email, user.email.lower())
        self.assertEqual(inv.invited_by, f"user:{self.admin.external_id}")
        self.assertFalse(StaffIdentity.objects.filter(user=user).exists())

    def test_admins_never_set_staff_passwords(self):
        response = self.api.post(
            USERS, new_user_payload(password="Some-Pass-1234"), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("send an e-mail", str(response.json()))
        self.assertFalse(User.objects.filter(username__startswith="inv").exists())

    def test_no_other_way_to_add_staff(self):
        from django.urls import resolve

        from ihr_identity.blocked import Blocked

        for path in (
            "/api/v1/users.json",
            "/api/v1/users/upsert/",
            "/api/v1/users/upsert.json",
        ):
            with self.subTest(path):
                self.assertIs(resolve(path).func.view_class, Blocked)
        bulk = {"datapoints": [new_user_payload()]}
        response = self.api.post("/api/v1/users/upsert/", bulk, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(User.objects.filter(username__startswith="inv").exists())

    def test_names_are_plain_text_and_invitations_are_capped(self):
        for field, value in (
            ("first_name", "Meera\nYour account is locked"),
            ("last_name", "see https://evil.example/"),
            ("first_name", "www.evil.example"),
        ):
            with self.subTest(value):
                response = self.api.post(
                    USERS, new_user_payload(**{field: value}), format="json"
                )
                self.assertEqual(response.status_code, 400)
        with mock.patch("ihr_identity.invitations.INVITES_PER_DAY", 1):
            self.invite()
            response = self.api.post(USERS, new_user_payload(), format="json")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(User.objects.filter(username__startswith="inv").count(), 1)

    # --- delivery (the provisioner) ------------------------------------------------

    def test_a_new_address_gets_the_setup_email(self):
        user, inv = self.invite()
        self.deliver()
        inv.refresh_from_db()
        self.assertEqual((inv.status, inv.kind), ("sent", "new"))
        sub = self.kc.created[inv.email]
        self.assertEqual(str(inv.keycloak_sub), sub)
        self.assertEqual(self.kc.members, [("testhosp", sub)])
        [(sent_sub, client_id, uri, seconds)] = self.kc.setup_mails
        self.assertEqual((sent_sub, client_id), (sub, "ihr-h-testhosp"))
        self.assertTrue(uri.endswith("/api/ihr/auth/callback/"))
        self.assertEqual(seconds, 72 * 3600)
        identity = StaffIdentity.objects.get(user=user)
        self.assertEqual(str(identity.keycloak_sub), sub)
        self.assertTrue(identity.awaiting_setup)
        self.assertFalse(identity.join_confirmation_required)
        self.assertEqual(mail.outbox, [])  # Keycloak sends this one
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_an_existing_account_gets_a_join_email(self):
        user, inv = self.invite()
        self.deliver(existing=[inv.email])
        inv.refresh_from_db()
        self.assertEqual((inv.status, inv.kind), ("sent", "join"))
        self.assertEqual(self.kc.setup_mails, [])
        self.assertEqual(self.kc.created, {})  # never a second account
        identity = StaffIdentity.objects.get(user=user)
        self.assertTrue(identity.join_confirmation_required)
        [message] = mail.outbox
        self.assertEqual(message.to, [inv.email])
        token = re.search(r"/api/ihr/join/\?t=([\w-]+)", message.body).group(1)
        self.assertEqual(inv.join_token_hash, invitations.token_hash(token))
        self.assertNotIn(token, str(vars(inv)))  # only its hash is kept

    def test_failures_are_recorded_and_retried_only_when_it_can_help(self):
        user, inv = self.invite()
        User.objects.filter(pk=user.pk).update(is_active=True)
        self.deliver()
        inv.refresh_from_db()
        self.assertEqual(
            (inv.status, inv.error), ("failed", "the invited user is already active")
        )

        _, inv2 = self.invite()
        boom = mock.patch(
            "ihr_platform.invitations._link", side_effect=RuntimeError("database away")
        )
        with boom:
            self.deliver()
        inv2.refresh_from_db()
        self.assertEqual((inv2.status, inv2.attempts), ("queued", 1))  # retried later
        self.assertIn("database away", inv2.error)
        with schema_context("public"):
            StaffInvitation.objects.filter(pk=inv2.pk).update(not_before=None)
        self.deliver()
        inv2.refresh_from_db()
        self.assertEqual(inv2.status, "sent")

    def test_an_account_this_hospital_made_and_the_person_set_up_must_join(self):
        """Never NEW (activated by a sign-in alone) once the person has set it up."""
        _, inv = self.invite()
        self.deliver()
        sub = str(inv.__class__.objects.get(pk=inv.pk).keycloak_sub)
        self.kc.set_up.add(sub)
        with schema_context("public"):
            StaffInvitation.objects.filter(pk=inv.pk).update(
                status="queued", sent_at=None, expires_at=None, not_before=None
            )
        StaffIdentity.objects.all().filter(keycloak_sub=sub).delete()
        self.deliver()
        inv.refresh_from_db()
        self.assertEqual(
            (inv.status, inv.kind, str(inv.keycloak_sub)), ("sent", "join", sub)
        )

    def test_another_hospitals_unused_account_waits_for_its_setup(self):
        user, inv = self.invite()
        self.kc.elsewhere[inv.email] = other = str(uuid.uuid4())
        self.deliver()
        inv.refresh_from_db()
        self.assertEqual(
            (inv.status, inv.attempts, inv.keycloak_sub), ("queued", 0, None)
        )
        self.assertIn("waiting", inv.error)
        self.assertEqual(self.kc.members, [])  # nothing granted while waiting
        self.assertFalse(StaffIdentity.objects.filter(user=user).exists())
        # Set up meanwhile: an ordinary join.
        self.kc.set_up.add(other)
        with schema_context("public"):
            StaffInvitation.objects.filter(pk=inv.pk).update(not_before=None)
        self.deliver()
        inv.refresh_from_db()
        self.assertEqual(
            (inv.status, inv.kind, str(inv.keycloak_sub)), ("sent", "join", other)
        )
        # And one that never gets set up gives up after three days.
        _, late = self.invite()
        self.kc.elsewhere[late.email] = str(uuid.uuid4())
        with schema_context("public"):
            StaffInvitation.objects.filter(pk=late.pk).update(
                created_at=timezone.now() - timedelta(hours=73)
            )
        self.deliver()
        late.refresh_from_db()
        self.assertEqual(late.status, "failed")

    def test_one_person_is_one_user_per_hospital(self):
        _, inv = self.invite()
        self.deliver(existing=[inv.email])
        _, inv2 = self.invite(email=inv.email.upper().replace("NEW", "NEW2", 1))
        with schema_context("public"):
            StaffInvitation.objects.filter(pk=inv2.pk).update(email=inv.email)
        members = list(self.kc.members)
        self.deliver(existing=[inv.email])  # the same account again
        inv2.refresh_from_db()
        self.assertEqual(
            (inv2.status, inv2.error),
            ("failed", "this person is already staff at this hospital"),
        )
        self.assertEqual(self.kc.members, members)  # refused before granting anything

    def test_the_join_email_keeps_names_plain(self):
        from ihr_platform.invitations import plain

        self.assertEqual(
            plain("Meera,\n\nVerify at https://x.example/now ok"),
            "Meera, Verify at  ok",
        )
        self.assertEqual(plain("go to www.x.example"), "go to")


class JoinTests(InvitationCase):
    """An existing account joining this hospital: link + sign-in + click, nothing less."""

    def setUp(self):
        super().setUp()
        self.user, self.inv = self.invite()
        self.deliver(existing=[self.inv.email])
        self.inv.refresh_from_db()
        self.sub = str(self.inv.keycloak_sub)
        self.link = re.search(r"(/api/ihr/join/\?t=[\w-]+)", mail.outbox[0].body).group(
            1
        )
        self.browser = APIClient(HTTP_HOST=HOST)

    def sign_in(self, browser=None, sub=None, **id_claims):
        browser = browser or self.browser
        start = browser.get("/api/ihr/auth/login/", {"next": "/x"})
        params = parse_qs(urlsplit(start["Location"]).query)
        state = sessions.unseal(
            browser.cookies[settings.IHR_LOGIN_COOKIE].value,
            settings.SECRET_KEY.encode(),
            "ihr-login",
        )
        sub = sub or self.sub
        tokens = testing.tokens_for(self.hospital, sub)
        tokens["id_token"] = testing.mint(
            self.hospital,
            sub,
            kind="ID",
            nonce=state["nonce"],
            email_verified=True,
            **id_claims,
        )
        with mock.patch.object(oidc, "exchange_code", return_value=tokens):
            return browser.get(
                "/api/ihr/auth/callback/", {"code": "c", "state": params["state"][0]}
            )

    def assert_not_joined(self):
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertTrue(
            StaffIdentity.objects.get(user=self.user).join_confirmation_required
        )

    def test_link_sign_in_and_click_join(self):
        opened = self.browser.get(self.link)
        self.assertEqual(
            opened["Location"],
            "/api/ihr/auth/login/?next=%2Fapi%2Fihr%2Fjoin%2Fconfirm%2F",
        )
        signed = self.sign_in()
        self.assertEqual(signed["Location"], "/api/ihr/join/confirm/")
        self.assertNotIn(settings.IHR_SESSION_COOKIE, signed.cookies)  # no session yet
        page = self.browser.get("/api/ihr/join/confirm/")
        html = page.content.decode()
        self.assertIn("Join ", html)
        self.assertIn("New Person", html)
        self.assertIn("frame-ancestors 'none'", page["Content-Security-Policy"])
        self.assert_not_joined()  # looking is not joining
        nonce = re.search(r'name="nonce" value="([^"]+)"', html).group(1)

        self.assertEqual(
            self.browser.post("/api/ihr/join/confirm/", {"nonce": "x"}).status_code, 403
        )
        cross = self.browser.post(
            "/api/ihr/join/confirm/", {"nonce": nonce}, HTTP_SEC_FETCH_SITE="cross-site"
        )
        self.assertEqual(cross.status_code, 403)
        self.assert_not_joined()

        done = self.browser.post(
            "/api/ihr/join/confirm/",
            {"nonce": nonce},
            HTTP_SEC_FETCH_SITE="same-origin",
        )
        self.assertEqual(done["Location"], "/api/ihr/auth/login/?next=%2F")
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        identity = StaffIdentity.objects.get(user=self.user)
        self.assertFalse(identity.awaiting_setup or identity.join_confirmation_required)
        self.inv.refresh_from_db()
        self.assertEqual((self.inv.status, self.inv.join_token_hash), ("accepted", ""))
        # Now an ordinary sign-in gives a session; the link is spent.
        self.assertEqual(self.sign_in()["Location"], "/x")
        again = APIClient(HTTP_HOST=HOST).get(self.link)
        self.assertEqual(again["Location"], "/login?error=join_link")

    def test_signing_in_without_the_link_never_joins(self):
        signed = self.sign_in()
        self.assertEqual(signed["Location"], "/login?error=join_link")
        self.assertEqual(
            self.browser.get("/api/ihr/join/confirm/")["Location"],
            "/login?error=join_link",
        )
        self.assert_not_joined()

    def test_someone_elses_link_does_not_work_for_you(self):
        self.browser.get(self.link)
        stranger = str(uuid.uuid4())
        signed = self.sign_in(sub=stranger)
        self.assertNotEqual(signed["Location"], "/api/ihr/join/confirm/")
        self.assert_not_joined()

    def test_an_expired_or_forged_link_does_not_work(self):
        self.assertEqual(
            self.browser.get("/api/ihr/join/?t=forged")["Location"],
            "/login?error=join_link",
        )
        with schema_context("public"):
            StaffInvitation.objects.filter(pk=self.inv.pk).update(
                expires_at=timezone.now() - timedelta(seconds=1)
            )
        self.assertEqual(
            self.browser.get(self.link)["Location"], "/login?error=join_link"
        )
        self.assert_not_joined()

    def test_a_verified_sign_in_alone_never_activates_a_join(self):
        """The first-sign-in activation of new accounts (P1-T5c) must skip joins."""
        self.sign_in()
        self.assert_not_joined()
