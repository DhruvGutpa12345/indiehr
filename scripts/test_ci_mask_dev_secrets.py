"""Tests for scripts/ci-mask-dev-secrets.py (P1-T1d). Run: python3 -m unittest scripts/test_ci_mask_dev_secrets.py

All values here are made up for the tests; no real or generated secret is used.
"""

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_spec = importlib.util.spec_from_file_location(
    "ci_mask_dev_secrets", Path(__file__).with_name("ci-mask-dev-secrets.py")
)
mask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mask)

ENV = """# a comment
KC_DB_PASSWORD=dbpass-111111
KC_BOOTSTRAP_ADMIN_USERNAME=tempadmin
KC_BOOTSTRAP_ADMIN_CLIENT_ID=temp-client
KC_BOOTSTRAP_ADMIN_CLIENT_SECRET=clientsecret-2222
IHR_S3_CARE_KEY=carekey-3333
IHR_AUDIT_ANCHOR_ENDPOINT=http://s3:8333
IHR_AUDIT_ANCHOR_BUCKET=audit-anchors
"""

USERS = """# Local test users

Generated 01-01-2026 by `scripts/care-dev.sh seed`. CARE's public fixture passwords were replaced.

## Alpha — http://alpha.localhost:8080

**Password for every user below:** `alpha-pass-4444`

## Beta — http://beta.localhost:8080

**Password for every user below:** `beta-pass-5555`

| Username | CARE role | Use it for |
|---|---|---|
| care-doctor | Doctor | consultations |

<!-- keycloak:start -->
## Keycloak (P1-T3) — staff sign-in

- Keycloak admin console: http://auth.localhost:8081/admin/ (realm `indiehr`) — temporary admin `tempadmin`, password `kc-admin-6666`

| Username | Name | Signs in as (hospital: CARE user) | Password | Authenticator key |
|---|---|---|---|---|
| dr.test | Test Doctor | alpha: care-doctor | `staff-pass-7777` | `APPKEY88888888` <!-- raw:0123456789abcdef --> |
| new.test | New Test | alpha: care-staff | `staff-pass-9999` | none yet: set up at first sign-in |

<!-- keycloak:end -->
"""


def tree(env: str | None = ENV, users: str | None = USERS) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "deploy" / "dev").mkdir(parents=True)
    (root / "deploy" / "seed").mkdir(parents=True)
    if env is not None:
        (root / "deploy" / "dev" / "keycloak.env").write_text(env)
    if users is not None:
        (root / "deploy" / "seed" / "TEST_USERS.md").write_text(users)
    return root


class CollectTests(unittest.TestCase):
    def test_current_shapes_give_every_secret_and_no_problem(self):
        values, problems = mask.collect(tree())
        self.assertEqual(problems, [])
        self.assertEqual(
            set(values),
            {
                "dbpass-111111",
                "clientsecret-2222",
                "carekey-3333",
                "alpha-pass-4444",
                "beta-pass-5555",
                "kc-admin-6666",
                "staff-pass-7777",
                "APPKEY88888888",
                "0123456789abcdef",
                "staff-pass-9999",
            },
        )
        for not_secret in ("tempadmin", "temp-client", "audit-anchors", "indiehr"):
            self.assertNotIn(not_secret, values)

    def test_missing_files_are_fine_before_they_are_written(self):
        self.assertEqual(mask.collect(tree(env=None, users=None)), ([], []))

    def test_raw_key_need_not_be_hex(self):
        users = USERS.replace("raw:0123456789abcdef", "raw:AbC123xyz789QQ")
        values, problems = mask.collect(tree(users=users))
        self.assertEqual(problems, [])
        self.assertIn("AbC123xyz789QQ", values)


class FailsClosedTests(unittest.TestCase):
    def assert_problem(self, root: Path, fragment: str):
        values, problems = mask.collect(root)
        self.assertTrue(
            any(fragment in problem for problem in problems),
            f"no problem containing {fragment!r} in {problems}",
        )
        for value in values:  # problems never carry a value
            self.assertFalse(any(value in problem for problem in problems), value)

    def test_unknown_env_key(self):
        self.assert_problem(
            tree(env=ENV + "IHR_NEW_TOKEN=tok-123456\n"), "IHR_NEW_TOKEN"
        )

    def test_database_url_style_key(self):
        self.assert_problem(
            tree(env=ENV + "DATABASE_URL=postgres://u:pw123456@db/x\n"), "DATABASE_URL"
        )

    def test_short_secret(self):
        self.assert_problem(tree(env=ENV + "OTHER_SECRET=abc\n"), "OTHER_SECRET")

    def test_password_cell_without_backticks(self):
        users = USERS.replace("`staff-pass-7777`", "staff-pass-7777")
        self.assert_problem(tree(users=users), "Password cell yields nothing")

    def test_key_cell_without_backticks(self):
        users = USERS.replace("`APPKEY88888888` ", "APPKEY88888888 ")
        self.assert_problem(tree(users=users), "Authenticator key cell yields nothing")

    def test_extra_column_moves_cells_but_still_found_by_header(self):
        users = USERS.replace(
            "| Password | Authenticator key |", "| Note | Password | Authenticator key |"
        ).replace("| `staff-pass-7777`", "| n | `staff-pass-7777`").replace(
            "| `staff-pass-9999`", "| n | `staff-pass-9999`"
        ).replace("|---|---|---|---|---|", "|---|---|---|---|---|---|")
        values, problems = mask.collect(tree(users=users))
        self.assertEqual(problems, [])
        self.assertIn("staff-pass-7777", values)

    def test_hospital_password_written_differently(self):
        users = USERS.replace("`alpha-pass-4444`", "alpha-pass-4444")
        self.assert_problem(tree(users=users), "hospital section without a password")

    def test_empty_raw_comment(self):
        users = USERS.replace("raw:0123456789abcdef", "raw:")
        self.assert_problem(tree(users=users), "raw: key that yields nothing")

    def test_file_with_nothing_in_it(self):
        self.assert_problem(tree(env=None, users="# nothing here\n"), "no value found")


class MainTests(unittest.TestCase):
    def run_main(self, root: Path):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with (
            mock.patch.object(mask, "ROOT", root),
            mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                mask.main()
            except SystemExit as exit_:
                code = exit_.code
        return code, out.getvalue(), err.getvalue()

    def test_prints_one_mask_per_value(self):
        code, out, err = self.run_main(tree())
        self.assertEqual(code, 0, err)
        self.assertIn("::add-mask::staff-pass-7777\n", out)
        self.assertEqual(out.count("::add-mask::"), 10)

    def test_masks_what_it_found_then_fails_without_printing_a_value(self):
        code, out, err = self.run_main(tree(env=ENV + "IHR_NEW_TOKEN=tok-123456\n"))
        self.assertEqual(code, 1)
        self.assertIn("::add-mask::dbpass-111111", out)
        self.assertIn("::add-mask::tok-123456", out)  # an unknown value is masked too
        self.assertIn("IHR_NEW_TOKEN", err)
        self.assertNotIn("tok-123456", err)

    def test_refuses_outside_actions(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": ""}):
            with self.assertRaises(SystemExit) as caught:
                mask.main()
        self.assertIn("Only for GitHub Actions", str(caught.exception.code))


if __name__ == "__main__":
    unittest.main()
