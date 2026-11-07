"""New staff accounts that the person sets up themselves from a one-time e-mail (P1-T5c).

The account is created with no password and no authenticator, so nobody can sign in with it.
Keycloak then e-mails the person a link that works once and expires: following it proves they
own the address, and they choose their password and set up their authenticator there. Nobody
else ever knows the password, and IndieHR never sees it.

An address that already has an account is never adopted: the person must confirm joining
another hospital themselves (staff invitations, P1-T5e).

Keycloak keeps every e-mailed link valid until it is used or expires, whatever happens to
the account's sessions (checked live: signing the user out does not cancel a link). Sending
the link again therefore replaces the never-used account (``delete_pending``): the earlier
links name the old account and die with it.
"""

from http import HTTPStatus
from urllib.parse import urlencode

from .kc_http import KeycloakError, q
from .realm import INVITED_FOR

# VERIFY_EMAIL completes silently on the e-mailed link (no second e-mail), and is the only way
# Keycloak 26.8 marks the address verified there: the other actions alone leave it unverified.
SETUP_ACTIONS = ["VERIFY_EMAIL", "UPDATE_PASSWORD", "CONFIGURE_TOTP"]


class ExistingAccountError(KeycloakError):
    """The address belongs to an account this invitation did not create."""


def _stamp(user):
    return (user.get("attributes") or {}).get(INVITED_FOR, [None])[0]


def find_invited(admin, sub, hospital_uuid):
    """The account ``sub`` if it was created for this hospital's invitation, else None."""
    resp = admin.call("GET", f"users/{sub}", ok=(200, 404))
    if resp.status != HTTPStatus.OK:
        return None
    user = resp.json()
    return user if _stamp(user) == hospital_uuid else None


def find_invited_by_email(admin, email, hospital_uuid):
    """The id of the account invited for this hospital under ``email``, or None."""
    for user in admin.get(f"users?email={q(email.strip().lower())}&exact=true"):
        if find_invited(admin, user["id"], hospital_uuid):
            return user["id"]
    return None


def account_state(admin, sub, hospital_uuid):
    """ "missing" (gone, or not this hospital's), "pending" (not set up) or "set_up"."""
    user = find_invited(admin, sub, hospital_uuid) if sub else None
    if user is None:
        return "missing"
    return "pending" if setup_pending(user) else "set_up"


def ensure_invited_user(admin, email, first, last, hospital_uuid, sub=None):
    """The id of the account invited for this hospital under ``email``, created if needed.

    Raises ExistingAccountError if the address (or a username equal to it) belongs to any
    other account. ``sub``: the id recorded earlier, if any."""
    email = email.strip().lower()
    if sub and find_invited(admin, sub, hospital_uuid):
        return sub
    found = {
        u["id"]: u
        for path in (
            f"users?email={q(email)}&exact=true",
            f"users?username={q(email)}&exact=true",
        )
        for u in admin.get(path)
    }
    for user in found.values():
        # Search results leave attributes out: read the account itself.
        if find_invited(admin, user["id"], hospital_uuid) is None:
            msg = (
                "This e-mail already has an IndieHR account. Joining an existing account "
                "to a new hospital comes with staff invitations (P1-T5e)."
            )
            raise ExistingAccountError(msg)
        return user["id"]
    resp = admin.post(
        "users",
        {
            "username": email,
            "email": email,
            "emailVerified": False,
            "firstName": first,
            "lastName": last,
            "enabled": True,
            "requiredActions": SETUP_ACTIONS,
            "attributes": {INVITED_FOR: [hospital_uuid]},
        },
    )
    created = admin.created_id(resp)
    if find_invited(admin, created, hospital_uuid) is None:
        # Keycloak drops attributes the user profile does not declare (realm not synced):
        # an unstamped account could never be found, resent or rolled back safely.
        admin.call("DELETE", f"users/{created}", ok=(204, 404))
        msg = f"Keycloak did not keep the {INVITED_FOR} stamp: run ihr_keycloak_sync --realm"
        raise KeycloakError(msg)
    return created


def setup_pending(user):
    """True while an invited account has never been set up: its address is not verified yet
    (following the setup link verifies it). An admin's reset of a set-up account
    (P1-T5e2) never counts: that account is someone's, and is never deleted or replaced
    as an unused invitation."""
    return not user.get("emailVerified") or "VERIFY_EMAIL" in (
        user.get("requiredActions") or []
    )


def send_setup_email(admin, sub, hospital_uuid, client_id, redirect_uri, seconds):
    """Ask Keycloak to e-mail the one-time setup link. False if there is nothing to send:
    the account is gone, not this hospital's, or already set up."""
    user = find_invited(admin, sub, hospital_uuid)
    if user is None or not setup_pending(user):
        return False
    query = urlencode(
        {"client_id": client_id, "redirect_uri": redirect_uri, "lifespan": seconds}
    )
    admin.put(f"users/{sub}/execute-actions-email?{query}", SETUP_ACTIONS)
    return True


def delete_pending(admin, sub, hospital_uuid):
    """Delete this hospital's invited account while its setup is unfinished (also part-way:
    whoever is part-way through used a link that is about to be replaced). False if it is
    set up or not this hospital's; True if it is gone."""
    user = find_invited(admin, sub, hospital_uuid)
    if user is None:
        return admin.call("GET", f"users/{sub}", ok=(200, 404)).status == 404  # noqa: PLR2004
    if not setup_pending(user):
        return False
    admin.call("DELETE", f"users/{sub}", ok=(204, 404))
    return True


def remove_invited_user(admin, sub, hospital_uuid):
    """Delete an account this hospital's invitation created, if it was never set up. True if
    it is gone. Never touches an account someone has set up or another hospital made."""
    user = find_invited(admin, sub, hospital_uuid)
    if user is None:
        return admin.call("GET", f"users/{sub}", ok=(200, 404)).status == 404  # noqa: PLR2004
    if admin.get(f"users/{sub}/credentials"):
        return False
    admin.call("DELETE", f"users/{sub}", ok=(204, 404))
    return True


RESET_ACTIONS = ["UPDATE_PASSWORD", "CONFIGURE_TOTP"]


def reset_sign_in(admin, sub, client_id, redirect_uri, seconds):
    """A hospital admin's reset for a lost authenticator (P1-T5e2).

    The password and the authenticator are both cleared: clearing only the authenticator
    would let anyone who knows the password sign in and enrol their own. Every sign-in ends
    (back-channel logout reaches every hospital), and a one-time link to set both again goes
    to the person's own verified address; nobody else sees it. Until it is used nobody can
    sign in with the account, which fails closed."""
    user = admin.get(f"users/{sub}")
    if not user.get("emailVerified"):
        msg = "the account's e-mail address is not verified: no reset link can go there"
        raise KeycloakError(msg)
    for credential in admin.get(f"users/{sub}/credentials"):
        if credential["type"] in ("password", "otp"):
            admin.delete(f"users/{sub}/credentials/{credential['id']}")
    # Once set up the account is the person's, not an invitation's: no hospital's stamp
    # may mark it as theirs to replace or delete any more.
    attributes = dict(user.get("attributes") or {})
    attributes.pop(INVITED_FOR, None)
    user["attributes"] = attributes
    user["requiredActions"] = RESET_ACTIONS
    admin.put(
        f"users/{sub}", user
    )  # the whole account: a partial update can drop fields
    admin.post(f"users/{sub}/logout", ok=(204,))
    query = urlencode(
        {"client_id": client_id, "redirect_uri": redirect_uri, "lifespan": seconds}
    )
    admin.put(f"users/{sub}/execute-actions-email?{query}", RESET_ACTIONS)


def account_exists(admin, sub):
    return admin.call("GET", f"users/{sub}", ok=(200, 404)).status == HTTPStatus.OK
