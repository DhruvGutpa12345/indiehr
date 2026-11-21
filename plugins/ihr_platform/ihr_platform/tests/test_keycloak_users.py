"""Generated passwords and Keycloak's block list (CI flake on c5055b4ce, 2026-11-22).

Keycloak loads ``passwordBlacklist(common-passwords.txt)`` into a Bloom filter that wrongly refuses
about 1 in 10,000 passwords (its start-up log: ``falsePositiveProbability=1.0E-4``). A random
password we generate for a synthetic login is never really a common one, so on that refusal we
draw a new one; any other refusal, and a third refusal in a row, still fails.
"""

from django.test import SimpleTestCase

from ihr_platform.keycloak.kc_http import KeycloakError
from ihr_platform.keycloak.users import create_user_with_new_password

BLOCKED = (
    'POST users: HTTP 400 {"error":"invalidPasswordBlacklistedMessage",'
    '"error_description":"Invalid password: password is not allowed."}'
)


class FakeAdmin:
    def __init__(self, refusals):
        self.refusals = list(refusals)
        self.posted = []

    def get(self, path):
        return []

    def delete(self, path):
        raise AssertionError("nothing to delete")

    def post(self, path, body):
        self.posted.append(body["credentials"][0]["value"])
        if self.refusals:
            raise KeycloakError(self.refusals.pop(0))
        return object()

    def created_id(self, resp):
        return "sub-1"


def counter():
    n = iter(range(100))
    return lambda: f"Ihr-generated-{next(n)}-7"


class CreateUserWithNewPasswordTests(SimpleTestCase):
    def test_a_block_list_refusal_draws_a_new_password(self):
        admin = FakeAdmin([BLOCKED])
        sub, password = create_user_with_new_password(
            admin, "dr.meera", "Meera", "Iyer", counter()
        )
        self.assertEqual(sub, "sub-1")
        self.assertEqual(admin.posted, ["Ihr-generated-0-7", "Ihr-generated-1-7"])
        self.assertEqual(password, "Ihr-generated-1-7")

    def test_three_refusals_in_a_row_still_fail(self):
        admin = FakeAdmin([BLOCKED, BLOCKED, BLOCKED])
        with self.assertRaises(KeycloakError):
            create_user_with_new_password(admin, "dr.meera", "Meera", "Iyer", counter())
        self.assertEqual(len(admin.posted), 3)

    def test_any_other_refusal_is_not_retried(self):
        admin = FakeAdmin(['POST users: HTTP 409 {"errorMessage":"User exists"}'])
        with self.assertRaises(KeycloakError):
            create_user_with_new_password(admin, "dr.meera", "Meera", "Iyer", counter())
        self.assertEqual(len(admin.posted), 1)
