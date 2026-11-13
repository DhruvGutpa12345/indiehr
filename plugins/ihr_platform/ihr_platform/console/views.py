"""The operator console (P1-T5b): plain server-rendered pages on the platform host.

    /ops/                          hospitals and recent provisioning runs
    /ops/hospitals/new/            the onboarding form -> queued for the provisioner
    /ops/operations/<id>/          step-by-step progress (refreshes itself), retry, roll back,
                                   the first admin's setup e-mail (sent, set up, send again)
    /ops/login/ /ops/callback/     operator sign-in (operator realm, password + code)
    /ops/logout/                   sign out (POST)

Pages queue work; only the provisioner process (ihr_provision_worker), which holds the
Keycloak admin credential, runs it. The console process never holds that credential.
"""

import base64
import hashlib
import hmac
import secrets
import time
from functools import wraps
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.db import OperationalError, connection, transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from ihr_identity import oidc
from ihr_identity.sessions import seal, unseal
from ihr_identity.tokens import TokenRejectedError, verify_id_token

from ihr_platform import release
from ihr_platform.console import auth
from ihr_platform.models import FleetRun, Hospital, TenantOperation
from ihr_platform.provisioning import runner, steps

LOGIN_STATE_SECONDS = 600
PROVISIONER_SILENT_SECONDS = (
    60  # queued this long untouched: say no provisioner is running
)
STYLE = """
body{font:15px/1.5 system-ui,sans-serif;margin:0;color:#1f2937;background:#f9fafb}
header{background:#0f172a;color:#fff;padding:.75rem 1.5rem;display:flex;gap:1rem;align-items:center}
header a{color:#fff}header form{margin-left:auto}main{padding:1.5rem;max-width:64rem}
table{border-collapse:collapse;width:100%;background:#fff}th,td{text-align:left;padding:.4rem .6rem;
border-bottom:1px solid #e5e7eb}label{display:block;margin-top:.6rem;font-weight:600}
input,select{font:inherit;padding:.35rem;width:100%;max-width:28rem;box-sizing:border-box}
button{font:inherit;padding:.45rem 1rem;margin-top:1rem;cursor:pointer}
.error{color:#b91c1c}.ok{color:#047857}.muted{color:#6b7280}.errorlist{color:#b91c1c}
"""


def _cookie(name, value, max_age):
    return {
        "key": name,
        "value": value,
        "max_age": max_age,
        "path": "/",
        "secure": settings.IHR_SESSION_COOKIE_SECURE,
        "httponly": True,
        "samesite": "Lax",  # set on the way back from Keycloak's top-level redirect
    }


def operator_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        session = auth.current_operator(request, active=request.GET.get("auto") != "1")
        if session is auth.UNAVAILABLE:
            return HttpResponse(
                "Operator sign-in is unavailable for a moment; please reload shortly.",
                status=503,
                content_type="text/plain; charset=utf-8",
            )
        if session is None:
            response = HttpResponseRedirect("/ops/login/")
            response.delete_cookie(settings.IHR_OPS_SESSION_COOKIE, path="/")
            return response
        request.operator = session
        return view(request, *args, **kwargs)

    return wrapped


@require_GET
def style(request):
    return HttpResponse(STYLE, content_type="text/css; charset=utf-8")


# --- sign-in ------------------------------------------------------------------------------


@require_GET
def login(request):
    verifier = secrets.token_urlsafe(48)
    state = {
        "state": secrets.token_urlsafe(24),
        "nonce": secrets.token_urlsafe(24),
        "verifier": verifier,
        "iat": time.time(),
    }
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    target = oidc.authorize_url(
        auth.realm(),
        client_id=settings.IHR_CONSOLE_CLIENT,
        redirect_uri=auth.redirect_uri(),
        response_type="code",
        scope="openid",
        state=state["state"],
        nonce=state["nonce"],
        code_challenge=challenge,
        code_challenge_method="S256",
    )
    response = HttpResponseRedirect(target)
    response.set_cookie(
        **_cookie(
            settings.IHR_OPS_LOGIN_COOKIE,
            seal(state, settings.SECRET_KEY.encode(), "ihr-ops-login"),
            LOGIN_STATE_SECONDS,
        )
    )
    return response


@require_GET
def callback(request):
    response = _finish_sign_in(request)
    response.delete_cookie(settings.IHR_OPS_LOGIN_COOKIE, path="/")
    return response


def _refused(reason):
    return HttpResponseRedirect(f"/ops/signed-out/?{urlencode({'reason': reason})}")


def _finish_sign_in(request):
    text = request.COOKIES.get(settings.IHR_OPS_LOGIN_COOKIE)
    state = (
        unseal(text, settings.SECRET_KEY.encode(), "ihr-ops-login") if text else None
    )
    if (
        not state
        or time.time() - state.get("iat", 0) > LOGIN_STATE_SECONDS
        or not hmac.compare_digest(str(request.GET.get("state", "")), state["state"])
    ):
        return _refused("expired")
    if "error" in request.GET or not request.GET.get("code"):
        return _refused("cancelled")
    try:
        tokens = oidc.exchange_code(
            request.GET["code"],
            state["verifier"],
            auth.redirect_uri(),
            realm=auth.realm(),
            client=settings.IHR_CONSOLE_CLIENT,
        )
        access = auth.check_token(tokens["access_token"])
        verify_id_token(
            tokens["id_token"],
            keys=oidc.signing_keys(realm=auth.realm()),
            issuer=oidc.issuer(auth.realm()),
            client_id=settings.IHR_CONSOLE_CLIENT,
            nonce=state["nonce"],
            access=access,
            now=time.time(),
            leeway=settings.IHR_OIDC_LEEWAY_SECONDS,
        )
    except (oidc.GrantRejectedError, TokenRejectedError, KeyError):
        return _refused("refused")
    except oidc.KeycloakUnavailableError:
        return _refused("unavailable")
    account = auth.operator_for(access["sub"])
    if account is None:
        oidc.end_keycloak_session(
            tokens["refresh_token"],
            realm=auth.realm(),
            client=settings.IHR_CONSOLE_CLIENT,
        )
        return _refused("not_operator")
    session = auth.create_session(access["sub"], account.name, tokens["refresh_token"])
    response = HttpResponseRedirect("/ops/")
    response.set_cookie(
        **_cookie(
            settings.IHR_OPS_SESSION_COOKIE,
            session.cookie,
            settings.IHR_SESSION_MAX_SECONDS,
        )
    )
    return response


@require_POST
def logout(request):  # Django's CSRF middleware checks the form token
    session = auth.load_session(request.COOKIES.get(settings.IHR_OPS_SESSION_COOKIE))
    everywhere = auth.end_session(session) if session else False
    response = _refused("signed_out" if everywhere else "signed_out_here")
    response.delete_cookie(settings.IHR_OPS_SESSION_COOKIE, path="/")
    return response


@require_GET
def signed_out(request):
    return render(
        request, "ihr_console/signed_out.html", {"reason": request.GET.get("reason")}
    )


# --- pages --------------------------------------------------------------------------------


@require_GET
@operator_required
def home(request):
    hospitals = list(
        Hospital.objects.exclude(schema_name="public")
        .select_related("schema_state")
        .order_by("slug")
    )
    code = release.code_fingerprint()
    for hospital in hospitals:  # why a hospital answers 503 (P1-T18a review S10)
        state = getattr(hospital, "schema_state", None)
        if state is not None and state.upgrading_since:
            hospital.release_note = (
                f"held since {timezone.localtime(state.upgrading_since):%d-%m-%Y %H:%M}"
                f" ({state.hold_reason or 'release'})"
            )
        elif state is None or state.schema_fingerprint != code:
            hospital.release_note = "schema not this build's"
        else:
            hospital.release_note = ""
    # Provisioning runs by default; a release adds one "upgrade" row per hospital
    # (ihr_fleet_run), shown with ?kind=upgrade next to the fleet runs themselves
    kind = request.GET.get("kind")
    upgrades = kind == TenantOperation.Kind.UPGRADE
    operations = TenantOperation.objects.order_by("-created_at")
    if upgrades:
        operations = operations.filter(kind=TenantOperation.Kind.UPGRADE)
    else:
        operations = operations.exclude(kind=TenantOperation.Kind.UPGRADE)
    return render(
        request,
        "ihr_console/home.html",
        {
            "operator": request.operator,
            "hospitals": hospitals,
            "operations": operations[:20],
            "upgrades": upgrades,
            "fleet_runs": FleetRun.objects.order_by("-started_at")[:10],
            "suffix": settings.IHR_TENANT_DOMAIN_SUFFIX,
        },
    )


class HospitalForm(forms.Form):
    slug = forms.CharField(
        max_length=31, help_text="Web address: <slug>.<platform domain>"
    )
    name = forms.CharField(max_length=200, label="Legal name (GSTIN holder)")
    display_name = forms.CharField(max_length=255)
    state_code = forms.ChoiceField(label="State")
    district = forms.CharField(max_length=100)
    city = forms.CharField(max_length=100)
    pin_code = forms.CharField(max_length=6, label="PIN code")
    phone = forms.CharField(max_length=14, help_text="+91…")
    address = forms.CharField(max_length=200)
    facility_name = forms.CharField(max_length=255)
    facility_type = forms.ChoiceField()
    admin_email = forms.EmailField(label="First administrator's e-mail")
    admin_first_name = forms.CharField(max_length=150)
    admin_last_name = forms.CharField(max_length=150)

    def __init__(self, *args, **kwargs):
        from care.facility.models.facility import REVERSE_REVERSE_FACILITY_TYPES
        from ihr_tenant_settings.india import GST_STATE_CODES

        super().__init__(*args, **kwargs)
        self.fields["state_code"].choices = [
            (code, name)
            for code, name in sorted(GST_STATE_CODES.items(), key=lambda i: i[1])
        ]
        self.fields["facility_type"].choices = [
            (name, name) for name in REVERSE_REVERSE_FACILITY_TYPES
        ]
        self.fields["facility_type"].initial = steps.DEFAULT_FACILITY_TYPE


@require_http_methods(["GET", "POST"])
@operator_required
def new_hospital(request):
    form = HospitalForm(request.POST or None)
    problem = None
    if request.method == "POST" and form.is_valid():
        try:
            op = runner.start(steps.OnboardingForm(**form.cleaned_data), _who(request))
        except steps.StepError as exc:
            problem = str(exc)
        else:
            _queue(op, "run", request)
            return HttpResponseRedirect(f"/ops/operations/{op.id}/")
    return render(
        request,
        "ihr_console/new_hospital.html",
        {"form": form, "problem": problem, "operator": request.operator},
    )


@require_GET
@operator_required
def operation(request, op_id):
    op = get_object_or_404(TenantOperation, pk=op_id)
    # "running" with nobody holding the hospital's lock: the provisioner stopped mid-run.
    stalled = (
        op.status == TenantOperation.Status.RUNNING
        and not op.queued_at
        and (not runner.is_busy(op.slug))
    )
    waiting = (
        bool(op.queued_at)
        and (timezone.now() - op.queued_at).total_seconds() > PROVISIONER_SILENT_SECONDS
    )
    busy = bool(op.queued_at) or (
        op.status == TenantOperation.Status.RUNNING and not stalled
    )
    resumable = (
        op.status
        in (
            TenantOperation.Status.FAILED,
            TenantOperation.Status.PENDING,
        )
        or stalled
    )
    can_rollback = (
        op.kind == TenantOperation.Kind.PROVISION
        and resumable
        and not busy
        and (op.hospital is None or op.hospital.activated_at is None)
    )
    return render(
        request,
        "ihr_console/operation.html",
        {
            "op": op,
            "busy": busy,
            "can_retry": op.kind == "provision" and resumable and not busy,
            "stalled": stalled,
            "waiting": waiting,
            "can_rollback": can_rollback,
            "total": round(sum(s.get("seconds") or 0 for s in op.steps), 1),
            "operator": request.operator,
            "address": f"{op.slug}.{settings.IHR_TENANT_DOMAIN_SUFFIX}",
            "setup": _first_admin_setup(op),
        },
    )


def _first_admin_setup(op):
    """Where the first admin's setup e-mail stands (P1-T5c), for a live hospital."""
    if (
        op.status != TenantOperation.Status.SUCCEEDED
        or op.hospital is None
        or "admin_user_id" not in op.result
    ):
        return None
    from care.users.models import User
    from django_tenants.utils import schema_context

    # Never into a schema held for a release or not on this build's (S10): it may be
    # migrating, and the read would wait behind its locks
    if not release.schema_ready(op.hospital):
        return {"upgrading": True}
    try:
        with schema_context(op.hospital.schema_name), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '2s'")
            done = User.objects.filter(
                pk=op.result["admin_user_id"], is_active=True
            ).exists()
    except OperationalError:  # a lock it could not get in time: show it as upgrading
        return {"upgrading": True}
    sent_at = op.result.get("setup_email_sent_at")
    return {
        "done": done,
        "sent_at": parse_datetime(sent_at) if sent_at else None,
        "hours": op.result.get("setup_link_hours"),
        "email": op.input.get("admin_email", ""),
        "problem": op.result.get("setup_resend_problem"),
    }


@require_POST
@operator_required
def retry(request, op_id):
    op = get_object_or_404(TenantOperation, pk=op_id, kind="provision")
    if op.status in ("failed", "pending", "running") and not op.queued_at:
        _queue(op, "run", request)  # the provisioner's lock refuses a run still going
    return HttpResponseRedirect(f"/ops/operations/{op.id}/")


@require_POST
@operator_required
def rollback(request, op_id):
    op = get_object_or_404(TenantOperation, pk=op_id, kind="provision")
    if op.status in ("failed", "pending", "running") and not op.queued_at:
        _queue(op, "rollback", request)
    return HttpResponseRedirect(f"/ops/operations/{op.id}/")


@require_POST
@operator_required
def resend_setup(request, op_id):
    op = get_object_or_404(
        TenantOperation, pk=op_id, kind="provision", status="succeeded"
    )
    if op.result.get("setup_email_sent_at") and not op.queued_at:
        _queue(op, "resend_setup", request)  # the provisioner checks it is still needed
    return HttpResponseRedirect(f"/ops/operations/{op.id}/")


def _who(request):
    return f"operator:{request.operator.sub} ({request.operator.name})"


def _queue(op, action, request):
    TenantOperation.objects.filter(pk=op.pk, queued_at__isnull=True).update(
        queued_action=action, queued_by=_who(request), queued_at=timezone.now()
    )
