"""Delivering staff invitations (P1-T5e1). Runs only in the provisioner process, the one
holding the Keycloak admin credential (``ihr_provision_worker``).

For each queued ``StaffInvitation``:
  - a new address: an account with no password or authenticator, stamped with the hospital,
    and Keycloak's one-time setup e-mail (the P1-T5c mechanism); the first sign-in activates;
  - an address that already has an account: hospital membership, and our own "join this
    hospital" e-mail with a one-time link (72 h). Activation then needs that link, a sign-in
    with password and code, and the "Join" click (ihr_identity.join).
Either way the CARE user is linked (``StaffIdentity``) and stays inactive until then.

Work for a hospital held for a release, or whose schema is not this build's (P1-T18a,
``release.py``), is never picked up and never failed: it waits, queued, until the hospital is
served again.
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
from ihr_audit import context as audit

from ihr_platform import release
from ihr_platform.models import Hospital, StaffInvitation

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
RETRY_SECONDS = 60
MAX_ERROR = 400


class InvitationError(Exception):
    """Delivery is impossible as things stand (shown to operators; no retry)."""


class HospitalNotReady(Exception):  # noqa: N818 (a state, not a failure)
    """The hospital is held for a release or not on this build's schema: wait (P1-T18a)."""


def _check_ready(hospital):
    if not release.schema_ready(hospital):
        raise HospitalNotReady


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
            StaffInvitation.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(status=StaffInvitation.Status.QUEUED)
            .filter(release.ready_q("hospital__"))
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
        with audit.job(
            {"job": "invitation", "invitation": inv.pk}, f"worker:for {inv.invited_by}"
        ):
            deliver(inv)
    except WaitForSetup:
        _wait(inv)
    except HospitalNotReady:
        _defer(inv)
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


def _defer(inv):
    """Held for a release since it was claimed: not an attempt, tried again later."""
    inv.attempts -= 1
    inv.not_before = timezone.now() + timedelta(seconds=RETRY_SECONDS)
    inv.save(update_fields=["attempts", "not_before"])


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
    _check_ready(hospital)  # before its schema is read (P1-T18a)
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
    """The staff worker's own narrow account (P1-T5f): accounts, memberships, e-mails."""
    from ihr_platform.management.commands.ihr_keycloak_sync import (
        keycloak_admin as admin,
    )

    return admin(purpose="staff")


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


# --- hospital admins' requests and expiry (P1-T5e2) -------------------------------------


class RequestError(Exception):
    """A request that cannot be carried out as things stand (shown to the admin)."""


def handle_request_once():
    """Carry out one queued StaffAccessRequest, if any. True if there was one.

    Claimed (RUNNING) and committed before Keycloak is called, outcome recorded after: a
    crash part-way never replays a reset (which wipes credentials and sends e-mail)."""
    from ihr_platform.models import StaffAccessRequest

    with transaction.atomic():
        req = (
            StaffAccessRequest.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(status=StaffAccessRequest.Status.QUEUED)
            .filter(release.ready_q("hospital__"))
            .order_by("created_at")
            .first()
        )
        if req is None:
            return False
        req.status = StaffAccessRequest.Status.RUNNING
        req.save(update_fields=["status"])
    status, error = StaffAccessRequest.Status.DONE, ""
    try:
        with audit.job(
            {"job": "staff_access", "request": str(req.pk), "action": req.action},
            f"worker:for {req.requested_by}",
        ):
            handle_request(req)
    except HospitalNotReady:
        # Nothing was done yet: back on the queue, picked up once the hospital is served
        StaffAccessRequest.objects.filter(pk=req.pk).update(
            status=StaffAccessRequest.Status.QUEUED
        )
        return True
    except (RequestError, InvitationError) as exc:
        status, error = StaffAccessRequest.Status.FAILED, str(exc)
    except Exception as exc:
        logger.exception("staff request %s failed", req.pk)
        status = StaffAccessRequest.Status.FAILED
        error = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    StaffAccessRequest.objects.filter(pk=req.pk).update(
        status=status, error=error[:MAX_ERROR], done_at=timezone.now()
    )
    return True


def handle_request(req):
    from ihr_platform.models import StaffAccessRequest

    hospital = req.hospital
    _check_ready(hospital)  # before its schema is read (P1-T18a)
    if hospital.status != Hospital.Status.ACTIVE or hospital.keycloak_org_id is None:
        raise RequestError("the hospital is not live")
    admin = keycloak_admin()
    if req.action == StaffAccessRequest.Action.RESET_SIGN_IN:
        _reset_sign_in(admin, req, hospital)
        return
    inv = StaffInvitation.objects.filter(
        hospital=hospital, care_user_id=req.care_user_id
    ).first()
    if inv is None:
        raise RequestError("this person has no invitation")
    if req.action == StaffAccessRequest.Action.RESEND_INVITATION:
        _resend(admin, inv)
    else:
        _withdraw(admin, inv, StaffInvitation.Status.CANCELLED)


def _reset_sign_in(admin, req, hospital):
    from django.contrib.auth import get_user_model
    from ihr_identity.models import StaffIdentity

    from ihr_platform.keycloak.hospital import client_id_for
    from ihr_platform.keycloak.invite import reset_sign_in
    from ihr_platform.management.commands.ihr_keycloak_sync import hospital_login

    with schema_context(hospital.schema_name):
        user = (
            get_user_model()
            .objects.filter(pk=req.care_user_id, deleted=False, is_active=True)
            .first()
        )
        identity = StaffIdentity.objects.filter(user=user).first() if user else None
    if identity is None or identity.awaiting_setup:
        raise RequestError("not active staff here: resend or cancel the invitation")
    if req.keycloak_sub and str(req.keycloak_sub) != str(identity.keycloak_sub):
        raise RequestError("the person's account changed since the request")
    reset_sign_in(
        admin,
        str(identity.keycloak_sub),
        client_id_for(hospital.slug),
        hospital_login(hospital).redirect_uris[0],
        settings.IHR_RESET_LINK_HOURS * 3600,
    )
    with schema_context(hospital.schema_name):
        # Back-channel logout ends the sessions everywhere; this also refuses any session
        # here that started before the reset, even if that message were lost.
        StaffIdentity.objects.filter(pk=identity.pk).update(
            sessions_valid_after=timezone.now()
        )


def _is_active(inv):
    from django.contrib.auth import get_user_model

    with schema_context(inv.hospital.schema_name):
        return (
            get_user_model()
            .objects.filter(pk=inv.care_user_id, is_active=True, deleted=False)
            .exists()
        )


def _close(inv, allowed, status=None):
    """Under row locks (the invitation, and the identity the join confirmation and the
    first sign-in update): re-check the invitation, then unlink the inactive user and, if
    given, set the new status. Committed before Keycloak is touched, so a "Join" click or a
    first sign-in racing with it either finished first (and this refuses) or finds no link
    (and refuses itself). Returns the locked, current invitation."""
    from ihr_identity.models import StaffIdentity

    with transaction.atomic():
        current = StaffInvitation.objects.select_for_update().get(pk=inv.pk)
        with schema_context(current.hospital.schema_name):
            list(
                StaffIdentity.objects.select_for_update().filter(
                    user_id=current.care_user_id
                )
            )
        if current.status not in allowed:
            msg = f"the invitation is {current.status}"
            raise RequestError(msg)
        if _is_active(current):
            # Never take membership from someone who already works here.
            raise RequestError("they are already active staff here")
        with schema_context(current.hospital.schema_name):
            StaffIdentity.objects.filter(
                user_id=current.care_user_id, user__is_active=False
            ).delete()
        if status:
            current.status = status
            current.join_token_hash = ""
            current.save(update_fields=["status", "join_token_hash"])
    return current


WITHDRAWABLE = {
    StaffInvitation.Status.SENT,
    StaffInvitation.Status.FAILED,
}


def _withdraw(admin, inv, status):
    """Cancel or expire an invitation. Access goes first (the link, in the database), then
    what it granted in Keycloak: an account made for it that nobody set up is deleted; an
    account someone uses keeps everything except this hospital's membership, and their
    other hospitals are left alone."""
    from ihr_platform.keycloak.hospital import hospital_ids, remove_member
    from ihr_platform.keycloak.invite import (
        account_exists,
        account_state,
        delete_pending,
    )

    inv = _close(inv, WITHDRAWABLE, status)
    sub = str(inv.keycloak_sub) if inv.keycloak_sub else None
    if not sub:
        return
    hospital = inv.hospital
    tag = str(hospital.uuid)
    deleted = (
        inv.kind == StaffInvitation.Kind.NEW
        and account_state(admin, sub, tag) == "pending"
        and delete_pending(admin, sub, tag)  # its membership goes with it
    )
    if not deleted and account_exists(admin, sub):
        ids = hospital_ids(admin, hospital.slug, str(hospital.keycloak_org_id))
        remove_member(admin, ids, sub, end_sessions=False)


def _resend(admin, inv):
    """A fresh e-mail. A new account's unused setup account is replaced (Keycloak cannot
    cancel a sent link, so earlier links die with it); a join gets a new link (only the new
    hash is kept). Cancelled, expired or failed invitations start over."""
    from ihr_platform.keycloak.invite import account_state, delete_pending

    _care_user(inv)  # still there and not active yet
    inv = _close(
        inv,
        WITHDRAWABLE
        | {StaffInvitation.Status.CANCELLED, StaffInvitation.Status.EXPIRED},
    )
    if (
        inv.status == StaffInvitation.Status.SENT
        and inv.kind == StaffInvitation.Kind.NEW
    ):
        tag = str(inv.hospital.uuid)
        state = account_state(admin, str(inv.keycloak_sub), tag)
        if state == "set_up":
            raise RequestError("they have set up their account: they can sign in now")
        if state == "pending" and not delete_pending(admin, str(inv.keycloak_sub), tag):
            raise RequestError("the unused account could not be replaced")
    StaffInvitation.objects.filter(pk=inv.pk, status=inv.status).update(
        status=StaffInvitation.Status.QUEUED,
        kind="",
        keycloak_sub=None,
        join_token_hash="",
        error="",
        expires_at=None,
        sent_at=None,
        not_before=None,
        attempts=0,
    )


EXPIRY_EVERY = 300
# A new account set up but never used to sign in here gets this long after its link expired
# before this hospital's membership is taken back (the account itself is theirs).
SET_UP_GRACE = timedelta(days=14)
_last_expiry = {"at": 0.0}


def expire_due(now=None):
    """Withdraw invitations not accepted in time (owner: clean up at expiry). Runs at most
    every few minutes. Returns how many expired."""
    import time

    from ihr_platform.keycloak.invite import account_state

    if now is None and time.monotonic() - _last_expiry["at"] < EXPIRY_EVERY:
        return 0
    _last_expiry["at"] = time.monotonic()
    now = now or timezone.now()
    # A hospital held for a release keeps its invitations until it is served (P1-T18a)
    due = (
        StaffInvitation.objects.filter(
            status=StaffInvitation.Status.SENT, expires_at__lt=now
        )
        .filter(release.ready_q("hospital__"))
        .select_related("hospital")
    )
    expired = 0
    admin = None
    for inv in due:
        admin = admin or keycloak_admin()
        try:
            if _is_active(inv):
                continue  # joined meanwhile; its own path marks it accepted
            if (
                inv.kind == StaffInvitation.Kind.NEW
                and inv.keycloak_sub
                and inv.expires_at < inv.sent_at + SET_UP_GRACE
                and account_state(admin, str(inv.keycloak_sub), str(inv.hospital.uuid))
                == "set_up"
            ):
                # Set up, not signed in here yet: a grace period, once.
                StaffInvitation.objects.filter(pk=inv.pk).update(
                    expires_at=now + SET_UP_GRACE
                )
                continue
            with audit.job(
                {"job": "invitation_expiry", "invitation": inv.pk}, "worker:expiry"
            ):
                _withdraw(admin, inv, StaffInvitation.Status.EXPIRED)
            expired += 1
        except Exception:  # the next sweep tries again
            logger.exception("invitation %s could not be expired", inv.pk)
    return expired
