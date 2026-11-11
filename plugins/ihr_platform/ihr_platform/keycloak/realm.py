"""The ``indiehr`` realm baseline: settings, login flows and hardening (ADR-008).

Login flows (aliases are realm-wide, so the per-use flows are copies of ``ihr-core``):

    ihr-core                     never bound; the shared login steps
      ihr-core-auth              REQUIRED
        Cookie                   ALTERNATIVE  (single sign-on)
        ihr-core-forms           ALTERNATIVE
          ihr-core-loa1          CONDITIONAL  level 1: username + password
          ihr-core-loa2          CONDITIONAL  level 2: OTP (enrolment forced if none yet)
    ihr-realm-browser            realm browser flow = core + "deny any hospital client"
    ihr-hospital-template        core + "deny unless <role>"; the role is a sentinel no one holds
    ihr-hospital-<slug>          copy of the template, bound to hospital <slug>'s client
    ihr-deny-all                 realm direct-grant flow: password grants are refused everywhere

The gates sit at the top level, after ``-auth``, so the single-sign-on cookie path meets them too.
Keycloak's role and client-scope conditions return "no match" when their role or scope cannot be
found, which would skip the Deny step (fail open). ``check_realm`` and ``hospital.check_hospital``
therefore verify every gate's configuration, and the hospital gate's role must resolve.
"""

import time
from http import HTTPStatus

from .kc_http import KeycloakError, q

FLOW_CORE = "ihr-core"
FLOW_REALM = "ihr-realm-browser"
FLOW_TEMPLATE = "ihr-hospital-template"
FLOW_DENY = "ihr-deny-all"
# "Forgot password?" (P1-T5e2): the e-mailed link alone never changes a password; the current
# authenticator code is asked too. Keycloak's built-in flow can instead remove the
# authenticator, and lets the person in without it.
FLOW_RESET = "ihr-reset-credentials"
HOSPITAL_SCOPE = "ihr-hospital"  # marker client scope carried by every hospital client
# User-profile attribute naming the hospital (its uuid) whose setup e-mail created an account
# (P1-T5c). Only admins can see or change it, so a person cannot claim another hospital's.
INVITED_FOR = "ihr_invited_for"
HOSPITAL_DEFAULT_SCOPES = [
    "basic",
    "acr",
    "roles",
    "profile",
    "email",
    "web-origins",
    HOSPITAL_SCOPE,
]
SENTINEL_ROLE = "ihr-gate-sentinel"  # realm role that nobody may hold
LOA_MAX_AGE = "43200"  # 12 h, the longest session (seconds)
OUR_KINDS = {
    "hospital",
    "console",
    "service",  # the workers' narrow admin accounts (P1-T5f, keycloak/accounts.py)
}  # client attribute ihr.kind of the clients we create
SERVICE_ACCOUNT_IDS = {"ihr-onboarding", "ihr-staff-worker"}
BUILT_IN_CLIENTS = {
    "account",
    "account-console",
    "admin-cli",
    "broker",
    "realm-management",
    "security-admin-console",
}
# Built-in clients a person can sign in to: they must also demand the second factor.
INTERACTIVE_BUILT_INS = ("account", "account-console", "security-admin-console")
_LEVEL = "conditional-level-of-authentication"
# (level, index, step, requirement, level config) of ihr-core, compared exactly: a copy of a
# half-built core would lose the OTP step yet look like its template.
CORE_SHAPE = [
    (0, 0, "flow", "REQUIRED", None),
    (1, 0, "auth-cookie", "ALTERNATIVE", None),
    (1, 1, "flow", "ALTERNATIVE", None),
    (2, 0, "flow", "CONDITIONAL", None),
    (
        3,
        0,
        _LEVEL,
        "REQUIRED",
        (("loa-condition-level", "1"), ("loa-max-age", LOA_MAX_AGE)),
    ),
    (3, 1, "auth-username-password-form", "REQUIRED", None),
    (2, 1, "flow", "CONDITIONAL", None),
    (
        3,
        0,
        _LEVEL,
        "REQUIRED",
        (("loa-condition-level", "2"), ("loa-max-age", LOA_MAX_AGE)),
    ),
    (3, 1, "auth-otp-form", "REQUIRED", None),
]
GATE_SHAPE = [
    (0, 1, "flow", "CONDITIONAL", None),
    (1, 0, None, "REQUIRED", None),  # the condition, checked by _check_gate
    (1, 1, "deny-access-authenticator", "REQUIRED", None),
]
DENY_SHAPE = [(0, 0, "deny-access-authenticator", "REQUIRED", None)]
RESET_OTP = "ihr-reset-otp"
RESET_DENY = "ihr-reset-no-otp"
RESET_SHAPE = [
    (0, 0, "reset-credentials-choose-user", "REQUIRED", None),
    (0, 1, "reset-credential-email", "REQUIRED", None),
    # The current code, for anyone who has an authenticator...
    (0, 2, "flow", "CONDITIONAL", None),
    (1, 0, "conditional-user-configured", "REQUIRED", None),
    (1, 1, "auth-otp-form", "REQUIRED", None),
    # ...and refused for anyone who has none (Keycloak would otherwise let them enrol one
    # with only the mailbox: an admin's reset in progress, an invitation not set up yet).
    (0, 3, "flow", "CONDITIONAL", None),
    (
        1,
        0,
        "conditional-sub-flow-executed",
        "REQUIRED",
        (("check_result", "not-executed"), ("flow_to_check", RESET_OTP)),
    ),
    (1, 1, "deny-access-authenticator", "REQUIRED", None),
    (0, 4, "reset-password", "REQUIRED", None),
]
MESSAGES = {
    "ihrNotMember": "You are not a member of this hospital.",
    "ihrHospitalLoginMissing": "This hospital's sign-in is not set up. Please contact support.",
}


def realm_settings(realm, smtp=None):
    """Realm flags we own. Compared on every check; nothing here is secret apart from an SMTP
    password, which is never compared (Keycloak hides it). ``smtp``: Keycloak's
    ``smtpServer`` fields for setup e-mails (P1-T5c); None leaves the realm's own alone."""
    rep = {
        "realm": realm,
        "displayName": "IndieHR",
        "enabled": True,
        "organizationsEnabled": True,
        "registrationAllowed": False,
        # "Forgot password?" (P1-T5e2, owner: self-service) only where a mail server is set:
        # the hospitals' realm. Pinned either way, so switching it on by hand is drift.
        "resetPasswordAllowed": bool(smtp),
        "rememberMe": False,
        "loginWithEmailAllowed": True,
        "duplicateEmailsAllowed": False,
        "editUsernameAllowed": False,
        "verifyEmail": False,
        # NIST SP 800-63B: length and a block list of common passwords, no composition rules.
        "passwordPolicy": (
            "length(12) and maxLength(128) and notUsername and notEmail"
            " and notContainsUsername and passwordHistory(5)"
            " and passwordBlacklist(common-passwords.txt)"
        ),
        "bruteForceProtected": True,
        "permanentLockout": False,
        "failureFactor": 5,
        "waitIncrementSeconds": 60,
        "minimumQuickLoginWaitSeconds": 60,
        "quickLoginCheckMilliSeconds": 1000,
        "maxFailureWaitSeconds": 900,
        "maxDeltaTimeSeconds": 43200,
        "otpPolicyType": "totp",
        "otpPolicyAlgorithm": "HmacSHA1",
        "otpPolicyDigits": 6,
        "otpPolicyPeriod": 30,
        "otpPolicyLookAheadWindow": 1,
        "otpPolicyCodeReusable": False,
        # Access tokens live 5 minutes (§4.6); sessions idle out after 30 min, end after 12 h.
        "accessTokenLifespan": 300,
        "accessCodeLifespan": 60,
        "ssoSessionIdleTimeout": 1800,
        "ssoSessionMaxLifespan": 43200,
        "revokeRefreshToken": True,
        "refreshTokenMaxReuse": 0,
        # Login and admin events, kept 180 days (CERT-In) in the Indian-region database.
        "eventsEnabled": True,
        "eventsExpiration": 15552000,
        # ... and as log lines in the server's journal (180 days; alerts on lock-out bursts:
        # P1-T9). Pinned so the drift check notices if someone removes it (P1-T8d1).
        "eventsListeners": ["jboss-logging"],
        "adminEventsEnabled": True,
        "adminEventsDetailsEnabled": True,
        # Hindi login pages need a reviewed translation (P1-T7); Keycloak ships none.
        "internationalizationEnabled": False,
        "attributes": {
            "acr.loa.map": '{"pwd":1,"mfa":2}',
            "adminEventsExpiration": "15552000",
        },
    }
    if smtp:
        # Keycloak stores every field as a string; booleans as "true"/"false".
        rep["smtpServer"] = {
            k: str(v).lower() if isinstance(v, bool) else str(v)
            for k, v in smtp.items()
        }
    return rep


def ensure_realm(admin, fix=True, smtp=None):
    """Create the realm if missing, then bring it to the baseline. Returns drift found."""
    realm = admin.realm
    exists = admin.call("GET", f"/{realm}", ok=(200, 404)).status == HTTPStatus.OK
    if not exists:
        admin.post("/", realm_settings(realm, smtp), ok=(201,))
    drift = [] if not exists else check_realm(admin, smtp=smtp)
    if exists and not fix:
        return drift

    admin.put(f"/{realm}", realm_settings(realm, smtp))
    admin.post("localization/en", MESSAGES, ok=(204, 200))
    _ensure_role(admin, SENTINEL_ROLE, "Gate sentinel: must never be assigned")
    _ensure_marker_scope(admin)
    _ensure_flows(admin)
    admin.put(
        f"/{realm}",
        {
            "browserFlow": FLOW_REALM,
            "directGrantFlow": FLOW_DENY,
            "resetCredentialsFlow": FLOW_RESET,
        },
    )
    _harden_built_in_clients(admin)
    _ensure_user_profile(admin)
    return drift


def check_realm(admin, hospital_client_ids=None, smtp=None):
    """Security-relevant differences from the baseline, as readable strings.

    ``hospital_client_ids``: every hospital client that should exist; any other client
    marked as a hospital's is then reported too (e.g. one left from a deleted hospital).
    """
    return (
        _check_settings(admin, smtp)
        + _check_profile(admin)
        + _check_guards(admin)
        + _check_flows(admin)
        + _check_clients(admin, hospital_client_ids)
    )


def shape_matches(shape, want):
    """``want`` entries with a None step match any step (the gate condition)."""
    return len(shape) == len(want) and all(
        (w[2] is None and s[:2] == w[:2] and s[3] == w[3]) or s == w
        for s, w in zip(shape, want, strict=True)
    )


def _check_settings(admin, smtp=None):
    issues = []
    rep = admin.get(f"/{admin.realm}")
    for key, want in realm_settings(admin.realm, smtp).items():
        if key == "attributes":
            for akey, aval in want.items():
                if rep.get("attributes", {}).get(akey) != aval:
                    issues.append(f"realm attribute {akey} is not {aval}")
        elif key == "smtpServer":
            have = rep.get("smtpServer") or {}
            for skey, sval in want.items():
                if skey != "password" and have.get(skey) != sval:
                    issues.append(f"realm mail server {skey} is not {sval!r}")
        elif rep.get(key) != want:
            issues.append(f"realm {key} is {rep.get(key)!r}, want {want!r}")
    if rep.get("browserFlow") != FLOW_REALM:
        issues.append(f"realm browser flow is {rep.get('browserFlow')!r}")
    if rep.get("directGrantFlow") != FLOW_DENY:
        issues.append(f"realm direct-grant flow is {rep.get('directGrantFlow')!r}")
    if rep.get("resetCredentialsFlow") != FLOW_RESET:
        issues.append(
            f"realm password-reset flow is {rep.get('resetCredentialsFlow')!r}"
        )
    return issues


def _check_guards(admin):
    issues = []
    if (
        admin.call("GET", f"roles/{SENTINEL_ROLE}", ok=(200, 404)).status
        != HTTPStatus.OK
    ):
        issues.append(
            f"sentinel role {SENTINEL_ROLE} is missing (gates would fail open)"
        )
    elif (
        admin.get(f"roles/{SENTINEL_ROLE}/users?first=0&max=1")
        or admin.get(f"roles/{SENTINEL_ROLE}/groups?first=0&max=1")
        or SENTINEL_ROLE in default_role_names(admin)
    ):
        issues.append(f"someone holds the sentinel role {SENTINEL_ROLE}")
    if not _scope_id(admin, HOSPITAL_SCOPE):
        issues.append(
            f"marker scope {HOSPITAL_SCOPE} is missing (gate would fail open)"
        )
    # Shared scopes reach every hospital's tokens: none may add roles, and the marker adds
    # nothing at all.
    scopes = {sc["name"]: sc["id"] for sc in admin.get("client-scopes")}
    for name in HOSPITAL_DEFAULT_SCOPES:
        if name not in scopes:
            issues.append(f"client scope {name} is missing")
            continue
        mappings = admin.get(f"client-scopes/{scopes[name]}/scope-mappings")
        if mappings.get("realmMappings") or mappings.get("clientMappings"):
            issues.append(f"client scope {name} adds roles to tokens")
    if HOSPITAL_SCOPE in scopes and admin.get(
        f"client-scopes/{scopes[HOSPITAL_SCOPE]}/protocol-mappers/models"
    ):
        issues.append(f"marker scope {HOSPITAL_SCOPE} adds claims to tokens")
    return issues


def _check_flows(admin):
    issues = []
    flows = {f["alias"] for f in admin.get("authentication/flows")}
    for alias in (FLOW_CORE, FLOW_REALM, FLOW_TEMPLATE, FLOW_DENY):
        if alias not in flows:
            issues.append(f"flow {alias} is missing")
    if FLOW_CORE in flows and flow_shape(admin, FLOW_CORE) != CORE_SHAPE:
        issues.append(f"flow {FLOW_CORE} is not password + OTP as built")
    if FLOW_DENY in flows and flow_shape(admin, FLOW_DENY) != DENY_SHAPE:
        issues.append(f"flow {FLOW_DENY} no longer refuses every password grant")
    if FLOW_RESET not in flows:
        issues.append(f"flow {FLOW_RESET} is missing")
    elif flow_shape(admin, FLOW_RESET) != RESET_SHAPE:
        issues.append(f"flow {FLOW_RESET} no longer asks for the authenticator code")
    for alias in (FLOW_REALM, FLOW_TEMPLATE):
        if alias in flows and not shape_matches(
            flow_shape(admin, alias), CORE_SHAPE + GATE_SHAPE
        ):
            issues.append(f"flow {alias} is not the core login plus its gate")
    if {FLOW_REALM, FLOW_TEMPLATE} <= flows:
        issues += _check_gate(
            admin,
            FLOW_REALM,
            "conditional-client-scope",
            {"client_scope": HOSPITAL_SCOPE, "negate": "false"},
            "ihrHospitalLoginMissing",
        )
        issues += _check_gate(
            admin,
            FLOW_TEMPLATE,
            "conditional-user-role",
            {"condUserRole": SENTINEL_ROLE, "negate": "true"},
            "ihrNotMember",
        )
    return issues


def _check_clients(admin, hospital_client_ids):
    issues = []
    for client in admin.get("clients"):
        cid = client["clientId"]
        attrs = client.get("attributes", {})
        if cid in INTERACTIVE_BUILT_INS and attrs.get("minimum.acr.value") != "mfa":
            issues.append(f"built-in client {cid} does not demand MFA")
        if cid in BUILT_IN_CLIENTS and client.get("authenticationFlowBindingOverrides"):
            issues.append(f"built-in client {cid} has its own login flow")
        if attrs.get("use.jwks.url") == "true":
            # Keycloak would trust any key that address returns (P1-T8d2): keys are pinned.
            issues.append(f"client {cid} takes its keys from a URL")
        kind = attrs.get("ihr.kind")
        if cid in BUILT_IN_CLIENTS:
            continue
        if kind not in OUR_KINDS:
            issues.append(f"client {cid} is neither built in nor a hospital client")
        elif kind == "service":
            if cid not in SERVICE_ACCOUNT_IDS:
                issues.append(f"client {cid} is an unknown service account")
        elif hospital_client_ids is not None and cid not in hospital_client_ids:
            issues.append(f"client {cid} belongs to no current hospital")
    return issues


def end_all_sessions(admin):
    """End every session in the realm and refuse every token issued before now (owner, Q-K20:
    the response to a suspected leak of the backend's key). People sign in again; a stolen
    refresh token stops working even if its thief also held the old key. Returns the realm's
    new "not before" time."""
    now = int(time.time())
    admin.put(f"/{admin.realm}", {"notBefore": now})
    admin.post("logout-all", ok=(200, 204))
    not_before = admin.get(f"/{admin.realm}").get("notBefore") or 0
    if not_before < now:
        msg = f"realm {admin.realm} kept notBefore {not_before}"
        raise KeycloakError(msg)
    return not_before


def default_role_names(admin, client_uuid=None):
    """Roles every user gets: realm ones, or one client's when ``client_uuid`` is given."""
    path = f"roles/default-roles-{admin.realm}/composites"
    path += f"/clients/{client_uuid}" if client_uuid else "/realm"
    return {r["name"] for r in admin.get(path)}


# --- flows ------------------------------------------------------------------------------


def flow_shape(admin, alias):
    """The flow's structure without aliases (copies rename every sub-flow)."""
    out = []
    for e in admin.get(f"authentication/flows/{q(alias)}/executions"):
        cfg = None
        if e.get("authenticationConfig") and e.get("providerId") in (
            "conditional-level-of-authentication",
            "conditional-sub-flow-executed",
        ):
            cfg = tuple(
                sorted(
                    admin.get(f"authentication/config/{e['authenticationConfig']}")[
                        "config"
                    ].items()
                )
            )
        kind = "flow" if e.get("authenticationFlow") else e.get("providerId")
        out.append((e["level"], e["index"], kind, e["requirement"], cfg))
    return out


def gate_execution(admin, alias, provider):
    """The single gate condition of a flow (top-level CONDITIONAL sub-flow) and its config."""
    execs = admin.get(f"authentication/flows/{q(alias)}/executions")
    gates = [e for e in execs if e.get("providerId") == provider]
    if len(gates) != 1:
        msg = f"flow {alias}: expected one {provider} step, found {len(gates)}"
        raise KeycloakError(msg)
    gate = gates[0]
    config = None
    if gate.get("authenticationConfig"):
        config = admin.get(f"authentication/config/{gate['authenticationConfig']}")
    return gate, config, execs


def _check_gate(admin, alias, provider, want_config, deny_message):
    issues = []
    try:
        gate, config, execs = gate_execution(admin, alias, provider)
    except KeycloakError as exc:
        return [str(exc)]
    if gate["requirement"] != "REQUIRED" or gate["level"] != 1:
        issues.append(f"flow {alias}: gate condition is not a REQUIRED level-1 step")
    if not config or any(config["config"].get(k) != v for k, v in want_config.items()):
        issues.append(
            f"flow {alias}: gate condition is not configured as {want_config}"
        )
    parent = [e for e in execs if e["level"] == 0 and e.get("authenticationFlow")]
    if not parent or parent[-1]["requirement"] != "CONDITIONAL":
        issues.append(
            f"flow {alias}: gate is not the last, CONDITIONAL, top-level step"
        )
    deny = [e for e in execs if e.get("providerId") == "deny-access-authenticator"]
    if len(deny) != 1 or deny[0]["requirement"] != "REQUIRED":
        issues.append(f"flow {alias}: gate has no REQUIRED deny step")
    elif deny[0].get("authenticationConfig"):
        msg = admin.get(f"authentication/config/{deny[0]['authenticationConfig']}")
        if msg["config"].get("denyErrorMessage") != deny_message:
            issues.append(f"flow {alias}: deny step message is not {deny_message}")
    return issues


def _flows(admin):
    return {f["alias"]: f for f in admin.get("authentication/flows")}


def _add_subflow(admin, parent, alias, requirement):
    admin.post(
        f"authentication/flows/{q(parent)}/executions/flow",
        {"alias": alias, "type": "basic-flow", "description": "", "provider": ""},
    )
    _set_requirement(
        admin, parent, lambda e: e.get("displayName") == alias, requirement
    )


def _add_step(admin, parent, provider, requirement, config=None, config_alias=None):
    resp = admin.post(
        f"authentication/flows/{q(parent)}/executions/execution", {"provider": provider}
    )
    exec_id = admin.created_id(resp)
    _set_requirement(admin, parent, lambda e: e.get("id") == exec_id, requirement)
    if config:
        admin.post(
            f"authentication/executions/{exec_id}/config",
            {"alias": config_alias, "config": config},
        )
    return exec_id


def _set_requirement(admin, flow, match, requirement):
    found = [
        e for e in admin.get(f"authentication/flows/{q(flow)}/executions") if match(e)
    ]
    if len(found) != 1:
        msg = f"flow {flow}: cannot find the step just added"
        raise KeycloakError(msg)
    rep = dict(found[0], requirement=requirement)
    admin.put(f"authentication/flows/{q(flow)}/executions", rep)


def _add_gate(admin, flow, condition, condition_config, deny_message):
    sub = f"{flow}-gate"
    _add_subflow(admin, flow, sub, "CONDITIONAL")
    _add_step(admin, sub, condition, "REQUIRED", condition_config, f"{sub}-condition")
    _add_step(
        admin,
        sub,
        "deny-access-authenticator",
        "REQUIRED",
        {"denyErrorMessage": deny_message},
        f"{sub}-deny",
    )


def _ensure_reset_flow(admin, flows):
    if FLOW_RESET in flows and flow_shape(admin, FLOW_RESET) != RESET_SHAPE:
        # Fail closed while it is rebuilt: password resets are refused meanwhile.
        admin.put(f"/{admin.realm}", {"resetCredentialsFlow": FLOW_DENY})
        admin.delete(f"authentication/flows/{flows.pop(FLOW_RESET)['id']}")
    if FLOW_RESET not in flows:
        admin.post(
            "authentication/flows",
            {
                "alias": FLOW_RESET,
                "description": "Forgot password: e-mailed link + authenticator code",
                "providerId": "basic-flow",
                "topLevel": True,
                "builtIn": False,
            },
        )
        _add_step(admin, FLOW_RESET, "reset-credentials-choose-user", "REQUIRED")
        _add_step(admin, FLOW_RESET, "reset-credential-email", "REQUIRED")
        _add_subflow(admin, FLOW_RESET, RESET_OTP, "CONDITIONAL")
        _add_step(admin, RESET_OTP, "conditional-user-configured", "REQUIRED")
        _add_step(admin, RESET_OTP, "auth-otp-form", "REQUIRED")
        _add_subflow(admin, FLOW_RESET, RESET_DENY, "CONDITIONAL")
        _add_step(
            admin,
            RESET_DENY,
            "conditional-sub-flow-executed",
            "REQUIRED",
            {"flow_to_check": RESET_OTP, "check_result": "not-executed"},
            f"{RESET_DENY}-condition",
        )
        _add_step(admin, RESET_DENY, "deny-access-authenticator", "REQUIRED")
        _add_step(admin, FLOW_RESET, "reset-password", "REQUIRED")


def _ensure_flows(admin):
    flows = _flows(admin)
    if FLOW_DENY not in flows:
        admin.post(
            "authentication/flows",
            {
                "alias": FLOW_DENY,
                "description": "Refuses every password grant",
                "providerId": "basic-flow",
                "topLevel": True,
                "builtIn": False,
            },
        )
        _add_step(admin, FLOW_DENY, "deny-access-authenticator", "REQUIRED")
        flows = _flows(admin)
    if FLOW_CORE in flows and flow_shape(admin, FLOW_CORE) != CORE_SHAPE:
        # Never bound, so it can be rebuilt in place (e.g. after an interrupted first run).
        admin.delete(f"authentication/flows/{flows.pop(FLOW_CORE)['id']}")
    for alias in (FLOW_REALM, FLOW_TEMPLATE):
        if alias in flows and not shape_matches(
            flow_shape(admin, alias), CORE_SHAPE + GATE_SHAPE
        ):
            if alias == FLOW_REALM:
                # Fail closed while the realm's login flow is rebuilt.
                admin.put(f"/{admin.realm}", {"browserFlow": FLOW_DENY})
            admin.delete(f"authentication/flows/{flows.pop(alias)['id']}")
    if FLOW_DENY in flows and flow_shape(admin, FLOW_DENY) != DENY_SHAPE:
        msg = f"flow {FLOW_DENY} was changed: fix it by hand (it is bound to the realm)"
        raise KeycloakError(msg)
    _ensure_reset_flow(admin, flows)
    if FLOW_CORE not in flows:
        admin.post(
            "authentication/flows",
            {
                "alias": FLOW_CORE,
                "description": "IndieHR login steps (never bound directly)",
                "providerId": "basic-flow",
                "topLevel": True,
                "builtIn": False,
            },
        )
        _add_subflow(admin, FLOW_CORE, "ihr-core-auth", "REQUIRED")
        _add_step(admin, "ihr-core-auth", "auth-cookie", "ALTERNATIVE")
        _add_subflow(admin, "ihr-core-auth", "ihr-core-forms", "ALTERNATIVE")
        for level, sub, step in (
            ("1", "ihr-core-loa1", "auth-username-password-form"),
            ("2", "ihr-core-loa2", "auth-otp-form"),
        ):
            _add_subflow(admin, "ihr-core-forms", sub, "CONDITIONAL")
            _add_step(
                admin,
                sub,
                "conditional-level-of-authentication",
                "REQUIRED",
                {"loa-condition-level": level, "loa-max-age": LOA_MAX_AGE},
                f"{sub}-level",
            )
            _add_step(admin, sub, step, "REQUIRED")
    for alias, condition, config, message in (
        (
            FLOW_REALM,
            "conditional-client-scope",
            {"client_scope": HOSPITAL_SCOPE, "negate": "false"},
            "ihrHospitalLoginMissing",
        ),
        (
            FLOW_TEMPLATE,
            "conditional-user-role",
            {"condUserRole": SENTINEL_ROLE, "negate": "true"},
            "ihrNotMember",
        ),
    ):
        if alias not in flows:
            admin.post(f"authentication/flows/{q(FLOW_CORE)}/copy", {"newName": alias})
            _add_gate(admin, alias, condition, config, message)


# --- roles, scopes, built-in clients, user profile --------------------------------------


def _ensure_role(admin, name, description):
    if (
        admin.call("GET", f"roles/{q(name)}", ok=(200, 404)).status
        == HTTPStatus.NOT_FOUND
    ):
        admin.post("roles", {"name": name, "description": description})


def _scope_id(admin, name):
    for scope in admin.get("client-scopes"):
        if scope["name"] == name:
            return scope["id"]
    return None


def _ensure_marker_scope(admin):
    if not _scope_id(admin, HOSPITAL_SCOPE):
        admin.post(
            "client-scopes",
            {
                "name": HOSPITAL_SCOPE,
                "description": "Marks a hospital login client; adds nothing to tokens",
                "protocol": "openid-connect",
                "attributes": {
                    "include.in.token.scope": "false",
                    "display.on.consent.screen": "false",
                },
            },
        )


def _harden_built_in_clients(admin):
    for client in admin.get("clients"):
        if client["clientId"] in INTERACTIVE_BUILT_INS:
            attrs = dict(client.get("attributes", {}), **{"minimum.acr.value": "mfa"})
            admin.put(f"clients/{client['id']}", dict(client, attributes=attrs))


ADMIN_ONLY = {"view": ["admin"], "edit": ["admin"]}


def _ensure_user_profile(admin):
    """Only admins change a user's email: it is a login name (TENANCY_DESIGN §4.5). The
    ``ihr_invited_for`` stamp is declared admin-only (undeclared attributes are dropped)."""
    profile = admin.get("users/profile")
    changed = False
    for attr in profile["attributes"]:
        if attr["name"] in ("email", "firstName", "lastName"):
            perms = attr.setdefault("permissions", {})
            if perms.get("edit") != ["admin"]:
                perms["edit"] = ["admin"]
                changed = True
    stamp = next((a for a in profile["attributes"] if a["name"] == INVITED_FOR), None)
    if stamp is None:
        profile["attributes"].append(
            {
                "name": INVITED_FOR,
                "displayName": "Invited for hospital",
                "permissions": ADMIN_ONLY,
                "multivalued": False,
            }
        )
        changed = True
    elif stamp.get("permissions") != ADMIN_ONLY:
        stamp["permissions"] = ADMIN_ONLY
        changed = True
    if changed:
        admin.put("users/profile", profile, ok=(200,))


def _check_profile(admin):
    issues = []
    attrs = {a["name"]: a for a in admin.get("users/profile")["attributes"]}
    for name in ("email", "firstName", "lastName"):
        if (attrs.get(name, {}).get("permissions") or {}).get("edit") != ["admin"]:
            issues.append(f"users can change their own {name}")
    if INVITED_FOR not in attrs:
        issues.append(f"user attribute {INVITED_FOR} is not declared")
    elif attrs[INVITED_FOR].get("permissions") != ADMIN_ONLY:
        issues.append(f"user attribute {INVITED_FOR} is not admin-only")
    return issues
