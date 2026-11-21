"""The staff "Sign-in" card's backend (P1-T24b): what the access GET offers, who may reset a
colleague's sign-in (attack A1), and fixed problem codes instead of worker text (attack A4).

- A1, the caller: only a hospital-wide admin (``staff_access.hospital_wide_admin``) may reset;
  anyone who may add staff (CARE's ``can_create_user``) still resends and cancels.
- A1, the target: a hospital-wide admin at any hospital is never reset from a hospital's
  screen. This hospital checks its own schema when asked; the provisioner checks every other
  hospital's before it touches Keycloak (``ProtectedElsewhere`` builds a second hospital).
- G2: ``offers`` is what POST accepts, per state; oneself gets none.
- A4: ``problem`` is a code from ``invitations.problem_code``, never the stored text.

Keycloak is faked as in test_invitations.
"""

import ast
import inspect
import time
import uuid
from unittest import mock

from care.emr.models.organization import Organization, OrganizationUser
from care.security.models import PermissionModel, RoleModel, RolePermission
from care.users.models import User
from django.db import connection
from django_tenants.utils import schema_context
from ihr_platform import invitations, release
from ihr_platform.models import (
    Domain,
    Hospital,
    HospitalSchemaState,
    StaffAccessRequest,
    StaffInvitation,
)
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner
from rest_framework.test import APIClient

from ihr_identity.models import StaffIdentity
from ihr_identity.staff_access import hospital_wide_admin
from ihr_identity.tests.test_invitations import HOST, RecoveryCase, make_hospital_wide

ACTIONS = ("reset-sign-in", "resend-invitation", "cancel-invitation")


def _name(prefix):
    return f"{prefix}{time.time_ns() % 10**10}"[:16]


class OffersCase(RecoveryCase):
    """RecoveryCase's fixtures (self.admin is a hospital-wide superuser) and helpers."""

    def staff(self, name):
        """A signed-in colleague who is not a superuser (a superuser is never a target)."""
        user = User.objects.create_user(username=_name(name), password="x-Unused-1")
        self.addCleanup(User.objects.filter(pk=user.pk).delete)
        client = APIClient(HTTP_HOST=HOST)
        client.force_authenticate(user)
        return user, client

    def lower_admin(self, name="low"):
        """An admin of an organisation inside the hospital (a district's, a team's): may add
        staff (``can_create_user``), not hospital-wide. A facility-only Facility Admin is the
        same case; ihr_access.tests.test_seed_admin checks that one on the role world."""
        user, client = self.staff(name)
        top = Organization.objects.create(name=f"top-{user.username}", org_type="role")
        self.addCleanup(Organization.objects.filter(pk=top.pk).delete)
        make_hospital_wide(self, user, parent=top)
        self.assertFalse(hospital_wide_admin(user))
        return user, client

    def last_request(self, target):
        return self.requests(target)[-1]

    def access(self, user, api=None):
        response = (api or self.api).get(f"/api/ihr/staff/{user.username}/access/")
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response.json()


class CallerRule(OffersCase):
    def test_only_a_hospital_wide_admin_may_reset(self):
        target, _ = self.active_staff()
        _low, low = self.lower_admin()
        page = self.access(target, low)
        self.assertEqual((page["state"], page["offers"]), ("active", []))
        refused = self.act(target, "reset-sign-in", low)
        self.assertEqual(refused.status_code, 403, refused.content)
        self.assertIn("hospital-wide admin", str(refused.json()))
        self.assertEqual(self.requests(target), [])  # nothing queued
        # the hospital-wide admin is offered it, and it is taken
        self.assertEqual(self.access(target)["offers"], ["reset-sign-in"])
        self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)

    def test_resend_and_cancel_keep_the_add_staff_rule(self):
        invited, _ = self.invite()
        self.deliver()
        _low, low = self.lower_admin()
        page = self.access(invited, low)
        self.assertEqual(page["state"], "sent")
        self.assertEqual(page["offers"], ["resend-invitation", "cancel-invitation"])
        self.assertEqual(self.act(invited, "cancel-invitation", low).status_code, 202)
        self.deliver()
        self.assertEqual(self.access(invited, low)["offers"], ["resend-invitation"])
        self.assertEqual(self.act(invited, "resend-invitation", low).status_code, 202)
        # someone who may not add staff gets nothing at all
        _plain, plain = self.staff("plain")
        self.assertEqual(
            plain.get(f"/api/ihr/staff/{invited.username}/access/").status_code, 403
        )

    def test_an_asker_who_lost_the_role_is_refused(self):
        target, _sub = self.active_staff()
        self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)
        OrganizationUser.objects.filter(user=self.admin).delete()
        self.deliver()
        self.assertEqual(self.kc.resets, [])
        req = self.last_request(target)
        self.assertEqual(
            (req.status, invitations.problem_code(req.error)), ("failed", "not-allowed")
        )

    def test_a_deactivated_deleted_or_service_asker_is_refused(self):
        """Review A3/K4: a queued reset can wait (a release hold re-queues it), and deactivating
        the admin who asked is the containment step for a suspected takeover; the membership
        rows stay, so the provisioner's re-check must look at the account itself."""
        for change in (
            {"is_active": False},
            {"deleted": True},
            {"is_service_account": True},
        ):
            with self.subTest(change=change):
                self.kc.resets.clear()
                target, _sub = self.active_staff()
                self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)
                User.objects.filter(pk=self.admin.pk).update(**change)
                try:
                    self.deliver()
                finally:
                    # get_entire_queryset: CARE's default manager hides deleted users, so a
                    # plain filter would leave the admin deleted for the next subtest
                    # (verifier late fix: the service-account case then tested nothing new)
                    User.objects.get_entire_queryset().filter(pk=self.admin.pk).update(
                        is_active=True, deleted=False, is_service_account=False
                    )
                self.assertTrue(hospital_wide_admin(self.admin))  # memberships kept
                self.assertEqual(self.kc.resets, [])
                req = self.last_request(target)
                self.assertEqual(
                    (req.status, invitations.problem_code(req.error)),
                    ("failed", "not-allowed"),
                )


class TargetRule(OffersCase):
    def test_a_hospital_wide_admin_here_is_never_reset(self):
        target, _ = self.active_staff()
        make_hospital_wide(self, target)
        page = self.access(target)  # a hospital-wide admin asks
        self.assertEqual((page["state"], page["offers"]), ("active", []))
        refused = self.act(target, "reset-sign-in")
        self.assertEqual(refused.status_code, 403, refused.content)
        self.assertEqual(self.requests(target), [])

    def test_oneself_is_offered_nothing(self):
        boss, client = self.staff("boss")
        make_hospital_wide(self, boss)
        page = self.access(boss, client)
        self.assertEqual(page["offers"], [])
        self.assertEqual(self.act(boss, "reset-sign-in", client).status_code, 400)


class OffersPerState(OffersCase):
    def states(self):
        """One colleague in each state the card knows (design "T24b as decided")."""
        active = self.active_staff()[0]  # first: it delivers whatever is queued
        queued, queued_inv = self.invite()
        sent, _ = self.invite()
        failed, failed_inv = self.invite()
        cancelled, cancelled_inv = self.invite()
        expired, expired_inv = self.invite()
        with schema_context("public"):  # kept out of this delivery: it stays being sent
            StaffInvitation.objects.filter(pk=queued_inv.pk).update(status="accepted")
        self.deliver()
        with schema_context("public"):
            StaffInvitation.objects.filter(pk=queued_inv.pk).update(status="queued")
            for inv, status in (
                (failed_inv, "failed"),
                (cancelled_inv, "cancelled"),
                (expired_inv, "expired"),
            ):
                StaffInvitation.objects.filter(pk=inv.pk).update(status=status)
        # linked by a setup e-mail, never signed in, nothing open: the first-admin path
        waiting, _ = self.staff("wait")
        User.objects.filter(pk=waiting.pk).update(is_active=False)
        StaffIdentity.objects.create(
            user=waiting, keycloak_sub=uuid.uuid4(), awaiting_setup=True
        )
        out = {
            "active": active,
            "queued": queued,
            "sent": sent,
            "failed": failed,
            "cancelled": cancelled,
            "expired": expired,
            "awaiting-setup": waiting,
            "none": self.staff("none")[0],  # active in CARE, never linked to a sign-in
        }
        for user in out.values():
            user.refresh_from_db()
        return out

    def test_offers_are_exactly_what_post_accepts(self):
        expected = {
            "active": ["reset-sign-in"],
            "awaiting-setup": [],
            "queued": [],
            "sent": ["resend-invitation", "cancel-invitation"],
            "failed": ["resend-invitation", "cancel-invitation"],
            "cancelled": ["resend-invitation"],
            "expired": ["resend-invitation"],
            "none": [],
        }
        states = self.states()
        self.assertEqual(sorted(states), sorted(expected))
        for state, user in states.items():
            with self.subTest(state=state):
                page = self.access(user)
                self.assertEqual(
                    (page["state"], page["offers"]), (state, expected[state])
                )
                for action in ACTIONS:
                    answer = self.act(user, action)
                    if action in expected[state]:
                        self.assertEqual(
                            answer.status_code, 202, (action, answer.content)
                        )
                    else:
                        self.assertIn(
                            answer.status_code, (400, 403), (action, answer.content)
                        )
                        self.assertNotEqual(answer.status_code, 202)


class ProblemCodes(OffersCase):
    def test_the_screen_gets_codes_never_the_workers_text(self):
        invited, inv = self.invite()
        raw = (
            "KeycloakError: 409 PUT /admin/realms/indiehr/users/"
            '8d1e1f9e-0000-4000-8000-000000000001 {"errorMessage":"secret detail"}'
        )
        with schema_context("public"):
            StaffInvitation.objects.filter(pk=inv.pk).update(status="failed", error=raw)
            StaffAccessRequest.objects.create(
                hospital=self.hospital,
                care_user_id=invited.pk,
                action=StaffAccessRequest.Action.RESEND_INVITATION,
                requested_by="user:test",
                status=StaffAccessRequest.Status.FAILED,
                error="the unused account could not be replaced",
            )
        response = self.api.get(f"/api/ihr/staff/{invited.username}/access/")
        page = response.json()
        self.assertEqual(page["invitation"]["problem"], "failed")
        self.assertEqual(page["requests"][0]["problem"], "not-replaced")
        for leak in (
            "admin/realms",
            "secret detail",
            "KeycloakError",
            "unused account",
        ):
            self.assertNotIn(leak, response.content.decode())
        with schema_context("public"):
            StaffInvitation.objects.filter(pk=inv.pk).update(error="")
        self.assertIsNone(self.access(invited)["invitation"]["problem"])

    def test_every_message_the_worker_writes_has_its_own_code(self):
        """Each literal message in invitations.py maps to a code other than the catch-all, so
        a new message must be given a code (and a phrase on the card) before it ships."""
        source = inspect.getsource(invitations)
        messages = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", "")
                index = {"RequestError": 0, "InvitationError": 0, "_failed": 1}.get(
                    name
                )
                if index is not None and len(node.args) > index:
                    arg = node.args[index]
                    if isinstance(arg, ast.Constant):
                        messages.add(arg.value)
            if isinstance(node, ast.Assign):
                names = {getattr(t, "id", getattr(t, "attr", "")) for t in node.targets}
                if not names & {"msg", "error"}:
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ):
                    messages.add(node.value.value)
                elif isinstance(
                    node.value, ast.JoinedStr
                ):  # f"the invitation is {status}"
                    head = node.value.values[0]
                    if isinstance(head, ast.Constant):
                        messages.add(head.value + "sent")
        messages |= {
            invitations.PROTECTED,
            invitations.UNCHECKED,
            invitations.NOT_ALLOWED,
            "waiting: the person has not yet set up the account another hospital made",
            "the address has another hospital's account that was never set up",
        }
        self.assertGreater(len(messages), 15)
        for message in sorted(messages):
            with self.subTest(message=message):
                self.assertNotEqual(invitations.problem_code(message), "failed")
        self.assertEqual(invitations.problem_code("ValueError: boom"), "failed")
        self.assertIsNone(invitations.problem_code(""))


class RefusalsAndLogs(OffersCase):
    """Review W3, K5, K6, K7, K8, K10 and A4 of P1-T24b."""

    def refusal(self, response):
        self.assertEqual(response.status_code, 400, response.content)
        return response.json()

    def test_every_400_carries_a_code_the_screen_can_translate(self):
        """W3: the card shows a phrase chosen by the code, never the backend's English."""
        boss, client = self.staff("boss")
        make_hospital_wide(self, boss)
        target, _ = self.active_staff()
        invited, _ = self.invite()  # queued: being sent
        codes = {
            "own": self.refusal(self.act(boss, "reset-sign-in", client)),
            "unknown-action": self.refusal(self.act(target, "do-something")),
            "not-active-yet": self.refusal(self.act(invited, "reset-sign-in")),
            "already-active": self.refusal(self.act(target, "cancel-invitation")),
            "being-sent": self.refusal(self.act(invited, "cancel-invitation")),
        }
        self.deliver()
        self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)
        codes["reset-recent"] = self.refusal(self.act(target, "reset-sign-in"))
        self.deliver()
        sent, _ = self.invite()
        self.deliver()
        self.act(sent, "cancel-invitation")
        self.deliver()
        codes["no-invitation"] = self.refusal(self.act(sent, "cancel-invitation"))
        for expected, body in codes.items():
            with self.subTest(code=expected):
                self.assertEqual(body["code"], expected, body)
                self.assertTrue(body["detail"])

    def test_the_daily_limit_does_not_name_another_hospital(self):
        """K10: this hospital must not learn that the reset was made elsewhere."""
        target, _ = self.active_staff()
        self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)
        self.deliver()
        body = self.refusal(self.act(target, "reset-sign-in"))
        self.assertNotIn("another hospital", body["detail"])
        self.assertNotIn("hospital", body["detail"].lower())

    def test_a_protected_refusal_counts_toward_the_daily_limit(self):
        """A4: repeating it is not free; a not-allowed one (the asker lost the role, nothing
        about the person) does not block another admin."""
        target, sub = self.active_staff()
        with schema_context("public"):
            StaffAccessRequest.objects.create(
                hospital=self.hospital,
                care_user_id=target.pk,
                keycloak_sub=sub,
                action=StaffAccessRequest.Action.RESET_SIGN_IN,
                requested_by="user:test",
                status=StaffAccessRequest.Status.FAILED,
                error=invitations.NOT_ALLOWED,
            )
        self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)
        self.deliver()
        with schema_context("public"):
            StaffAccessRequest.objects.filter(care_user_id=target.pk).update(
                status=StaffAccessRequest.Status.FAILED, error=invitations.PROTECTED
            )
        before = len(self.requests(target))
        body = self.refusal(self.act(target, "reset-sign-in"))
        self.assertEqual(body["code"], "protected-recent")
        self.assertEqual(len(self.requests(target)), before)  # nothing queued

    def test_final_wait_failure_is_not_shown_as_waiting(self):
        """K6: a failed invitation never reads "Waiting for setup"."""
        final = "the address has another hospital's account that was never set up"
        mid = "waiting: the person has not yet set up the account another hospital made"
        self.assertEqual(invitations.problem_code(mid), "waiting-for-setup")
        self.assertEqual(invitations.problem_code(final), "other-account-not-set-up")

    def test_cancelling_clears_the_old_problem(self):
        """K7: a cancelled or expired invitation does not keep a stale problem."""
        invited, inv = self.invite()
        self.deliver()
        with schema_context("public"):
            StaffInvitation.objects.filter(pk=inv.pk).update(
                status="failed", error="waiting: the person has not yet set up"
            )
        self.assertEqual(self.act(invited, "cancel-invitation").status_code, 202)
        self.deliver()
        page = self.access(invited)
        self.assertEqual(page["state"], "cancelled")
        self.assertIsNone(page["invitation"]["problem"])

    def test_a_final_delivery_failure_is_logged(self):
        """K8: the raw text stays in the record and in the provisioner's log."""
        _invited, inv = self.invite()
        failure = invitations.InvitationError("the invited user no longer exists")
        with (
            mock.patch.object(invitations, "deliver", side_effect=failure),
            self.assertLogs("ihr_platform.invitations", level="INFO") as logs,
            schema_context("public"),
        ):
            invitations.deliver_once()
        self.assertTrue(
            any(
                f"invitation {inv.pk} failed: user-gone (the invited user no longer exists)"
                in line
                for line in logs.output
            ),
            logs.output,
        )

    def test_refusals_leave_a_stable_line(self):
        """K5: ``ihr-staff: hospital=<slug> request=<id> action=<a> outcome=<code>``, from the
        provisioner (protected, not-allowed...) and from the web 403s (no request row)."""
        target, _ = self.active_staff()
        _low, low = self.lower_admin()
        with self.assertLogs("ihr_platform.invitations", level="INFO") as logs:
            self.assertEqual(self.act(target, "reset-sign-in", low).status_code, 403)
        self.assertIn(
            f"ihr-staff: hospital={self.hospital.slug} request=none "
            "action=reset_sign_in outcome=reset-refused",
            [line.split(":", 2)[2] for line in logs.output],
        )
        admin_target, _ = self.active_staff()
        make_hospital_wide(self, admin_target)
        with self.assertLogs("ihr_platform.invitations", level="INFO") as logs:
            self.assertEqual(self.act(admin_target, "reset-sign-in").status_code, 403)
        self.assertIn(
            f"ihr-staff: hospital={self.hospital.slug} request=none "
            "action=reset_sign_in outcome=admin-target",
            [line.split(":", 2)[2] for line in logs.output],
        )
        # the provisioner: a reset whose asker lost the role
        self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)
        OrganizationUser.objects.filter(user=self.admin).delete()
        with self.assertLogs("ihr_platform.invitations", level="INFO") as logs:
            self.deliver()
        req = self.last_request(target)
        self.assertIn(
            f"ihr-staff: hospital={self.hospital.slug} request={req.pk} "
            "action=reset_sign_in outcome=not-allowed",
            [line.split(":", 2)[2] for line in logs.output],
        )


class ProtectedElsewhere(OffersCase):
    """A second hospital, its own schema, committed and dropped at the end. The person who is
    active staff here is a hospital-wide admin there: the provisioner refuses the reset."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = f"{time.time_ns() % 10**12:x}"
        cls.addClassCleanup(connection.set_schema, TEST_SCHEMA)
        with as_owner():
            connection.set_schema_to_public()
            cls.other = Hospital(
                slug=f"po{tag}"[:31],
                name="Other Hospital",
                status=Hospital.Status.ACTIVE,
            )
            cls.other.save()
            cls.addClassCleanup(cls._drop)
            Domain.objects.create(domain=f"po{tag}.offers.test", tenant=cls.other)
            release.record(cls.other)
        connection.set_schema(TEST_SCHEMA)

    @classmethod
    def _drop(cls):
        with as_owner(), schema_context("public"), connection.cursor() as cursor:
            Domain.objects.filter(tenant=cls.other).delete()
            Hospital.objects.filter(pk=cls.other.pk).delete()
            cursor.execute(f'DROP SCHEMA IF EXISTS "{cls.other.schema_name}" CASCADE')

    def there(self, sub, *, admin, superuser=False):
        """The same person (Keycloak ``sub``) as a CARE user of the other hospital."""
        with as_owner(), schema_context(self.other.schema_name):
            user = User.objects.create_user(
                username=_name("there"), password="x-Unused-1", is_superuser=superuser
            )
            StaffIdentity.objects.create(user=user, keycloak_sub=sub)
            if admin:
                role = RoleModel.objects.create(name=f"role-{user.username}")
                permission, _ = PermissionModel.objects.get_or_create(
                    slug="can_create_user", defaults={"name": "can_create_user"}
                )
                RolePermission.objects.create(role=role, permission=permission)
                OrganizationUser.objects.create(
                    organization=Organization.objects.create(name=f"o-{user.username}"),
                    user=user,
                    role=role,
                )
        return user

    def test_an_admin_elsewhere_is_never_reset_from_here(self):
        target, sub = self.active_staff()
        self.there(sub, admin=True)
        # Known limit: this hospital cannot see the other one, so the button is offered
        self.assertEqual(self.access(target)["offers"], ["reset-sign-in"])
        self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)
        self.deliver()
        self.assertEqual(self.kc.resets, [])  # Keycloak never touched
        req = self.last_request(target)
        self.assertEqual(
            (req.status, invitations.problem_code(req.error)), ("failed", "protected")
        )
        response = self.api.get(f"/api/ihr/staff/{target.username}/access/")
        self.assertEqual(response.json()["requests"][0]["problem"], "protected")
        for leak in (self.other.slug, self.other.name, self.other.schema_name):
            self.assertNotIn(leak, response.content.decode())

    def other_as(self, status, *, recorded=True):
        """The other hospital in ``status`` for this test; without its HospitalSchemaState row
        if not ``recorded`` (a hospital being provisioned before its schema step)."""
        with as_owner(), schema_context("public"):
            Hospital.objects.filter(pk=self.other.pk).update(status=status)
            if not recorded:
                HospitalSchemaState.objects.filter(pk=self.other.pk).delete()

        def back():
            with as_owner(), schema_context("public"):
                Hospital.objects.filter(pk=self.other.pk).update(
                    status=Hospital.Status.ACTIVE
                )
                release.record(Hospital.objects.get(pk=self.other.pk))

        self.addCleanup(back)

    def test_an_admin_at_a_hospital_not_live_is_protected_too(self):
        """Review A1/K2: "any hospital" is every hospital whose schema is kept migrated
        (fleet.MIGRATE_STATUSES), not only ACTIVE ones: a suspended or offboarding hospital's
        admin needs the same single sign-in when it resumes, and a provisioning hospital's
        first admin may already work elsewhere."""
        for status in (
            Hospital.Status.SUSPENDED,
            Hospital.Status.OFFBOARDING,
            Hospital.Status.PROVISIONING,
        ):
            with self.subTest(status=status):
                self.kc.resets.clear()
                self.other_as(status)
                target, sub = self.active_staff()
                self.there(sub, admin=True)
                self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)
                self.deliver()
                self.assertEqual(self.kc.resets, [])
                req = self.last_request(target)
                self.assertEqual(
                    (req.status, invitations.problem_code(req.error)),
                    ("failed", "protected"),
                )

    def test_a_hospital_still_before_its_schema_step_is_skipped(self):
        """A provisioning hospital with no recorded schema holds no StaffIdentity yet (the
        first admin is linked after the schema step): it must not fail every reset closed."""
        self.other_as(Hospital.Status.PROVISIONING, recorded=False)
        target, sub = self.active_staff()
        self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)
        self.deliver()
        self.assertEqual([r[0] for r in self.kc.resets], [sub])
        self.assertEqual(self.last_request(target).status, "done")

    def test_an_archived_hospital_is_not_read(self):
        self.other_as(Hospital.Status.ARCHIVED)
        target, sub = self.active_staff()
        self.there(sub, admin=True)
        self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)
        self.deliver()
        self.assertEqual([r[0] for r in self.kc.resets], [sub])

    def test_a_superuser_elsewhere_is_protected(self):
        """Review A2/K3: here a superuser is never a target (``_target``); elsewhere too."""
        target, sub = self.active_staff()
        self.there(sub, admin=False, superuser=True)
        self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)
        self.deliver()
        self.assertEqual(self.kc.resets, [])
        req = self.last_request(target)
        self.assertEqual(
            (req.status, invitations.problem_code(req.error)), ("failed", "protected")
        )

    def test_plain_staff_elsewhere_is_reset(self):
        target, sub = self.active_staff()
        self.there(sub, admin=False)
        self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)
        self.deliver()
        self.assertEqual([r[0] for r in self.kc.resets], [sub])
        self.assertEqual(self.last_request(target).status, "done")

    def test_a_hospital_it_cannot_read_fails_closed(self):
        target, _sub = self.active_staff()
        real = release.schema_ready
        with mock.patch.object(
            release,
            "schema_ready",
            lambda h: False if h.pk == self.other.pk else real(h),
        ):
            self.assertEqual(self.act(target, "reset-sign-in").status_code, 202)
            self.deliver()
        self.assertEqual(self.kc.resets, [])
        req = self.last_request(target)
        self.assertEqual(
            (req.status, invitations.problem_code(req.error)), ("failed", "try-later")
        )
