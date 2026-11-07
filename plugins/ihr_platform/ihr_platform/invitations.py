"""Delivering staff invitations (P1-T5e1). Runs only in the provisioner process, the one
holding the Keycloak admin credential (``ihr_provision_worker``).

For each queued ``StaffInvitation``:
  - a new address: an account with no password or authenticator, stamped with the hospital,
    and Keycloak's one-time setup e-mail (the P1-T5c mechanism); the first sign-in activates;
  - an address that already has an account: hospital membership, and our own "join this
    hospital" e-mail with a one-time link (72 h). Activation then needs that link, a sign-in
    with password and code, and the "Join" click (ihr_identity.join).
Either way the CARE user is linked (``StaffIdentity``) and stays inactive until then.
"""

import hashlib
import logging
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django_tenants.utils import schema_context

from ihr_platform.models import Hospital, StaffInvitation

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
RETRY_SECONDS = 60
MAX_ERROR = 400


class InvitationError(Exception):
    """Delivery is impossible as things stand (shown to operators; no retry)."""


URL_LIKE = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)\S*")


def plain(text, limit=80):
    """One line of text with nothing that reads as a link: names go into our e-mails."""
    return URL_LIKE.sub("", " ".join(str(text).split()))[:limit].strip()


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def claim_next():
    """Take the oldest invitation due for delivery (row lock, skip locked), or None."""
    now = timezone.now()
    with transaction.atomic():
        inv = (
            StaffInvitation.objects.select_for_update(skip_locked=True)
            .filter(status=StaffInvitation.Status.QUEUED)
            .exclude(not_before__gt=now)
            .order_by("created_at")
            .first()
        )
        if inv is None:
            return None
        inv.attempts += 1
        # Not picked up again while this attempt runs (a crash retries after the delay).
        inv.not_before = now + timedelta(seconds=RETRY_SECONDS * inv.attempts)
        inv.save(update_fields=["attempts", "not_before"])
    return inv


def deliver_once():
    """Deliver one due invitation, if any. True if there was one."""
    inv = claim_next()
    if inv is None:
        return False
    try:
        deliver(inv)
    except WaitForSetup:
        _wait(inv)
    except InvitationError as exc:
        _failed(inv, str(exc), final=True)
    except Exception as exc:  # recorded; tried again after a delay, up to MAX_ATTEMPTS
        logger.exception("invitation %s not delivered", inv.pk)
        _failed(inv, f"{type(exc).__name__}: {exc}", final=inv.attempts >= MAX_ATTEMPTS)
    return True


def _wait(inv):
    if timezone.now() - inv.created_at > WAIT_MAX:
        _failed(
            inv,
            "the address has another hospital's account that was never set up",
            final=True,
        )
        return
    inv.attempts -= 1  # waiting is not a failed attempt
    inv.not_before = timezone.now() + timedelta(seconds=WAIT_SECONDS)
    inv.error = (
        "waiting: the person has not yet set up the account another hospital made"
    )
    inv.save(update_fields=["attempts", "not_before", "error"])


def _failed(inv, error, *, final):
    inv.error = error.replace("\n", " ")[:MAX_ERROR]
    if final:
        inv.status = StaffInvitation.Status.FAILED
    inv.save(update_fields=["error", "status"])


def deliver(inv):
    """Every check that can refuse comes first; Keycloak membership and the link come only
    once the invitation will be sent (review of P1-T5e1)."""
    from ihr_platform.keycloak.hospital import ensure_member, hospital_ids

    hospital = inv.hospital
    if hospital.status != Hospital.Status.ACTIVE or hospital.keycloak_org_id is None:
        raise InvitationError("the hospital is not live")
    user = _care_user(inv)
    admin = keycloak_admin()
    tag = str(hospital.uuid)
    sub, kind = _account_for(admin, inv, tag)
    _check_link(hospital, user, sub)
    if (str(inv.keycloak_sub or ""), inv.kind) != (sub, kind):
        inv.keycloak_sub, inv.kind = sub, kind
        inv.save(update_fields=["keycloak_sub", "kind"])
    ensure_member(
        admin, hospital_ids(admin, hospital.slug, str(hospital.keycloak_org_id)), sub
    )
    _link(inv, hospital, user, sub)
    if inv.kind == StaffInvitation.Kind.NEW:
        _send_setup(admin, inv, hospital, sub)
    else:
        _send_join(inv, hospital)
    inv.status = StaffInvitation.Status.SENT
    inv.sent_at = timezone.now()
    inv.error = ""
    inv.save(update_fields=["status", "sent_at", "error"])


def keycloak_admin():
    from ihr_platform.management.commands.ihr_keycloak_sync import (
        keycloak_admin as admin,
    )

    return admin()


def _account_for(admin, inv, tag):
    """(sub, kind) from the account's state now, never only from an earlier attempt.

    - no account: one made for this invitation, NEW (setup e-mail; first sign-in activates);
    - this hospital's own unused account: NEW again;
    - any account someone has set up, also one this hospital once made: JOIN (link, sign-in
      and the click);
    - another hospital's account that was never set up: wait. Linking to it now would bind
      this hospital to an account that hospital may replace, and lets one hospital block an
      address for everyone; the person sets it up from that hospital's e-mail first.
    """
    from ihr_platform.keycloak.invite import (
        ExistingAccountError,
        ensure_invited_user,
        find_invited,
        setup_pending,
    )
    from ihr_platform.keycloak.realm import INVITED_FOR

    try:
        sub = ensure_invited_user(admin, inv.email, inv.first_name, inv.last_name, tag)
    except ExistingAccountError:
        sub = _existing_account(admin, inv.email)
        account = admin.get(f"users/{sub}")
        stamp = (account.get("attributes") or {}).get(INVITED_FOR)
        if stamp and setup_pending(account):
            raise WaitForSetup from None
        return sub, StaffInvitation.Kind.JOIN
    own = find_invited(admin, sub, tag)
    if own is None:
        raise InvitationError("the account made for this invitation is gone")
    if setup_pending(own):
        return sub, StaffInvitation.Kind.NEW
    return sub, StaffInvitation.Kind.JOIN


class WaitForSetup(Exception):  # noqa: N818 (a state, not a failure)
    """The address belongs to another hospital's account that is not set up yet."""


WAIT_SECONDS = 3600
WAIT_MAX = timedelta(hours=72)


def _care_user(inv):
    from django.contrib.auth import get_user_model

    with schema_context(inv.hospital.schema_name):
        user = (
            get_user_model()
            .objects.filter(
                pk=inv.care_user_id, deleted=False, is_service_account=False
            )
            .first()
        )
    if user is None:
        raise InvitationError("the invited user no longer exists")
    if user.is_active:
        raise InvitationError("the invited user is already active")
    return user


def _existing_account(admin, email):
    """The id of the account that already uses this address (as e-mail or username)."""
    from ihr_platform.keycloak.kc_http import q

    for path in (
        f"users?email={q(email)}&exact=true",
        f"users?username={q(email)}&exact=true",
    ):
        found = admin.get(path)
        if found:
            return found[0]["id"]
    msg = "the address's account disappeared"
    raise InvitationError(msg)


def _check_link(hospital, user, sub):
    from ihr_identity.models import StaffIdentity

    with schema_context(hospital.schema_name):
        if StaffIdentity.objects.filter(keycloak_sub=sub).exclude(user=user).exists():
            # One person, one CARE user per hospital.
            raise InvitationError("this person is already staff at this hospital")
        linked = StaffIdentity.objects.filter(user=user).first()
        if linked is not None and str(linked.keycloak_sub) != str(sub):
            raise InvitationError("the user is linked to another account")


def _link(inv, hospital, user, sub):
    from ihr_identity.models import StaffIdentity

    with schema_context(hospital.schema_name), transaction.atomic():
        identity, _ = StaffIdentity.objects.get_or_create(
            user=user,
            defaults={
                "keycloak_sub": sub,
                "awaiting_setup": True,
                "join_confirmation_required": inv.kind == StaffInvitation.Kind.JOIN,
            },
        )
        if str(identity.keycloak_sub) != str(sub):
            raise InvitationError("the user is linked to another account")


def _send_setup(admin, inv, hospital, sub):
    """Sent first, recorded after: a crash in between re-sends at worst (both links go to
    the same mailbox), never leaves the person without one."""
    from ihr_platform.keycloak.hospital import client_id_for
    from ihr_platform.keycloak.invite import send_setup_email
    from ihr_platform.management.commands.ihr_keycloak_sync import hospital_login

    if inv.expires_at:
        return  # already sent; resending replaces the account (P1-T5e2)
    hours = settings.IHR_SETUP_LINK_HOURS
    sent = send_setup_email(
        admin,
        sub,
        str(hospital.uuid),
        client_id_for(hospital.slug),
        hospital_login(hospital).redirect_uris[0],
        hours * 3600,
    )
    if not sent:
        raise InvitationError("the account is already set up")
    inv.expires_at = timezone.now() + timedelta(hours=hours)
    inv.save(update_fields=["expires_at"])


def _send_join(inv, hospital):
    """Sent first, its hash recorded after: if the record is lost, that link simply does not
    work and the next attempt sends a new one."""
    from ihr_tenant_settings.models import HospitalProfile

    from ihr_platform.links import frontend_base_url

    if inv.expires_at:
        return
    hours = settings.IHR_SETUP_LINK_HOURS
    token = secrets.token_urlsafe(32)
    with schema_context(hospital.schema_name):
        name = plain(HospitalProfile.current().display_name or hospital.name)
        link = f"{frontend_base_url().rstrip('/')}/api/ihr/join/?t={token}"
    send_mail(
        subject=f"Join {name} on IndieHR",
        message=(
            f"Hello {plain(inv.first_name, 40) or 'there'},\n\n"
            f"{name} has invited you to work with them on IndieHR. You already have an "
            "IndieHR account, so you do not need a new password.\n\n"
            "To accept, open this link, sign in with your password and code, and confirm:\n"
            f"{link}\n\n"
            f"The link works once and expires in {hours} hours. If you were not expecting "
            "this, ignore this e-mail: nothing changes unless you sign in and confirm.\n"
        ),
        from_email=None,
        recipient_list=[inv.email],
    )
    inv.join_token_hash = token_hash(token)
    inv.expires_at = timezone.now() + timedelta(hours=hours)
    inv.save(update_fields=["join_token_hash", "expires_at"])
