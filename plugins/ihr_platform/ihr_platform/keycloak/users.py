"""Creating staff accounts directly (dev seed and tests only).

Production staff join through an organisation invitation (TENANCY_DESIGN §4.5, P1-T5), never
with a password someone else chose.
"""

import json

from . import totp
from .kc_http import KeycloakError, q

BLOCK_LIST_REFUSAL = "invalidPasswordBlacklistedMessage"


def create_user_with_new_password(
    admin,
    username,
    first,
    last,
    make_password,
    otp_secret=None,
    *,
    replace=True,
    tries=3,
):
    """``create_user`` with a password from ``make_password()``; returns (sub, password).

    Only for passwords we generate at random. Keycloak checks its block list with a Bloom filter
    that wrongly refuses about 1 in 10,000 passwords (``falsePositiveProbability=1.0E-4`` in its
    start-up log), so on that refusal a new password is drawn, at most ``tries`` times. Any other
    refusal is raised at once."""
    for attempt in range(tries):
        password = make_password()
        try:
            sub = create_user(
                admin, username, first, last, password, otp_secret, replace=replace
            )
        except KeycloakError as exc:
            if BLOCK_LIST_REFUSAL not in str(exc) or attempt == tries - 1:
                raise
            continue
        return sub, password
    raise AssertionError("unreachable")


def create_user(
    admin, username, first, last, password, otp_secret=None, *, replace=True
):
    """(Re)create one user with a password and, optionally, a TOTP credential.
    With ``replace=False`` an existing user of that name is an error, never deleted."""
    for old in admin.get(f"users?username={q(username)}&exact=true"):
        if not replace:
            msg = f"Keycloak user {username} already exists"
            raise KeycloakError(msg)
        admin.delete(f"users/{old['id']}")
    credentials = [{"type": "password", "value": password, "temporary": False}]
    if otp_secret:
        credentials.append(
            {
                "type": "otp",
                "userLabel": "Demo authenticator",
                "secretData": json.dumps({"value": otp_secret}),
                "credentialData": json.dumps(
                    {
                        "subType": "totp",
                        "digits": 6,
                        "counter": 0,
                        "period": totp.PERIOD,
                        "algorithm": "HmacSHA1",
                    }
                ),
            }
        )
    resp = admin.post(
        "users",
        {
            "username": username,
            "email": f"{username}@staff.example.test",
            "emailVerified": True,
            "firstName": first,
            "lastName": last,
            "enabled": True,
            "credentials": credentials,
        },
    )
    return admin.created_id(resp)
