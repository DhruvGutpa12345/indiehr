"""Creating staff accounts directly (dev seed and tests only).

Production staff join through an organisation invitation (TENANCY_DESIGN §4.5, P1-T5), never
with a password someone else chose.
"""

import json

from . import totp
from .kc_http import q


def create_user(admin, username, first, last, password, otp_secret=None):
    """(Re)create one user with a password and, optionally, a TOTP credential."""
    for old in admin.get(f"users?username={q(username)}&exact=true"):
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
