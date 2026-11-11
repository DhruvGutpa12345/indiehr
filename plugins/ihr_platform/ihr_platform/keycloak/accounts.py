"""The provisioners' own narrow Keycloak accounts (P1-T5f, ADR-008; owner: two processes).

Until now the provisioner used the bootstrap admin of the ``master`` realm, which reaches every
realm, the operators' included. Instead each worker now holds one service account of the
hospitals' realm, with only the realm-management roles its calls need (measured live on
Keycloak 26.8):

    ihr-onboarding     builds hospitals: organisation, client, gate flow, first admin. Needs
                       manage-realm (flows), so it can change this realm, never another.
    ihr-staff-worker   invitations, resets, memberships and expiry: accounts, organisation
                       membership and e-mails only. It cannot change realm settings, clients
                       or flows, give itself roles, or reach any other realm.

Both are confidential clients with no browser or password sign-in, "full scope" off and only
the admin-API audience in their tokens (Keycloak 26.8 refuses admin calls without it).
They are made and drift-checked by ``ihr_keycloak_sync --realm`` (the operator's bootstrap
credential). Each proves itself with an assertion signed by its own key (P1-T8d2, ADR-012),
which only its own container holds; Keycloak holds the public half only, so neither Keycloak's
database nor the sync's output can be used to act as a worker. Remaining reach, by design: the
staff worker can change any staff account in this realm (it must clear credentials for
resets), so its key is as sensitive as a hospital admin's password.
"""

from .hospital import client_key_attributes
from .jwks import same_keys
from .kc_http import KeycloakError, q

KIND = "service"
ADMIN_API_SCOPE = "kc-admin-api"
_READ = ("view-users", "query-users", "view-clients", "query-clients")
_ORGS = ("manage-organizations", "view-organizations", "query-organizations")
ACCOUNTS = {
    "ihr-onboarding": frozenset(
        ("manage-realm", "view-realm", "manage-clients", "manage-users", *_READ, *_ORGS)
    ),
    "ihr-staff-worker": frozenset(("manage-users", *_READ, *_ORGS)),
}
PURPOSES = {"onboarding": "ihr-onboarding", "staff": "ihr-staff-worker"}


def _client_rep(client_id, jwks):
    return {
        "clientId": client_id,
        "name": f"IndieHR {client_id.removeprefix('ihr-')} (service account)",
        "description": "Narrow admin account of an IndieHR worker (P1-T5f)",
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": False,
        "bearerOnly": False,
        "serviceAccountsEnabled": True,
        "standardFlowEnabled": False,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "consentRequired": False,
        "fullScopeAllowed": False,
        # Signed assertions with keys pinned in jwks.string: never a key URL someone else
        # could point at, and no secret that Keycloak (or its backups) could give away.
        "clientAuthenticatorType": "client-jwt",
        "redirectUris": [],
        "webOrigins": [],
        "attributes": {
            "ihr.kind": KIND,
            "oauth2.device.authorization.grant.enabled": "false",
            "oidc.ciba.grant.enabled": "false",
            "standard.token.exchange.enabled": "false",
            # One-minute admin tokens (the realm's are five): after a suspected theft of a
            # worker's key, tokens minted with it die within a minute of the rotation. The
            # worker fetches a new one by itself (P1-T8d2 review).
            "access.token.lifespan": "60",
            **client_key_attributes(jwks),
        },
    }


def _find(admin, client_id):
    found = admin.get(f"clients?clientId={q(client_id)}")
    return found[0] if found else None


def _realm_management(admin):
    client = _find(admin, "realm-management")
    roles = {r["name"]: r for r in admin.get(f"clients/{client['id']}/roles")}
    return client["id"], roles


def _scope_ids(admin):
    return {s["name"]: s["id"] for s in admin.get("client-scopes")}


def _jwks_of(jwks_by_account, client_id):
    jwks = (jwks_by_account or {}).get(client_id)
    if not jwks:
        msg = f"no public key set for {client_id} (keys ensure, IHR_CLIENT_JWKS_DIR)"
        raise KeycloakError(msg)
    return jwks


def ensure_service_accounts(admin, jwks_by_account):
    """Create or correct both accounts: settings, keys, exactly their roles, exactly the
    admin-API scope. ``jwks_by_account`` maps each account to its worker's public key set.

    An account still on a client secret (before P1-T8d2) gets a new, never-read secret as it
    switches: the old one was exported to a worker's environment file and must not work again,
    even if someone switched the account back."""
    for client_id in ACCOUNTS:
        _jwks_of(jwks_by_account, client_id)  # all keys known before anything changes
    rm_id, rm_roles = _realm_management(admin)
    scopes = _scope_ids(admin)
    if ADMIN_API_SCOPE not in scopes:
        msg = f"client scope {ADMIN_API_SCOPE} is missing (Keycloak 26.8+ provides it)"
        raise KeycloakError(msg)
    for client_id, wanted in ACCOUNTS.items():
        client = _find(admin, client_id)
        if client is None:
            admin.post(
                "clients", _client_rep(client_id, _jwks_of(jwks_by_account, client_id))
            )
            client = _find(admin, client_id)
        elif client.get("attributes", {}).get("ihr.kind") != KIND:
            msg = f"client {client_id} exists and is not ours"
            raise KeycloakError(msg)
        cid = client["id"]
        rep = _client_rep(client_id, _jwks_of(jwks_by_account, client_id))
        admin.put(f"clients/{cid}", rep | {"id": cid})
        _kill_old_secret(admin, cid, client.get("clientAuthenticatorType"))
        sa = admin.get(f"clients/{cid}/service-account-user")["id"]
        _exact_roles(admin, f"users/{sa}/role-mappings", rm_id, rm_roles, wanted)
        _exact_roles(admin, f"clients/{cid}/scope-mappings", rm_id, rm_roles, wanted)
        for path in (
            f"users/{sa}/role-mappings/realm",
            f"clients/{cid}/scope-mappings/realm",
        ):
            if extra := admin.get(path):
                admin.call("DELETE", path, body=extra, ok=(204,))
        # Roles of any other client (a hospital's member role, broker, account): none.
        mappings = admin.get(f"users/{sa}/role-mappings").get("clientMappings") or {}
        for name, mapping in mappings.items():
            if name != "realm-management":
                admin.call(
                    "DELETE",
                    f"users/{sa}/role-mappings/clients/{mapping['id']}",
                    body=mapping["mappings"],
                    ok=(204,),
                )
        for kind in ("default", "optional"):
            for scope in admin.get(f"clients/{cid}/{kind}-client-scopes"):
                if not (kind == "default" and scope["name"] == ADMIN_API_SCOPE):
                    admin.delete(f"clients/{cid}/{kind}-client-scopes/{scope['id']}")
        admin.put(f"clients/{cid}/default-client-scopes/{scopes[ADMIN_API_SCOPE]}")


def _kill_old_secret(admin, cid, was):
    """An account that was on a client secret gets a new one nobody ever reads."""
    if was != "client-jwt":
        # The response holds the new secret: it is dropped unread, never shown.
        admin.post(f"clients/{cid}/client-secret", ok=(200,))


def _exact_roles(admin, base, rm_id, rm_roles, wanted):
    path = f"{base}/clients/{rm_id}"
    have = {r["name"] for r in admin.get(path)}
    if missing := wanted - have:
        admin.post(path, [rm_roles[n] for n in sorted(missing)], ok=(204,))
    if extra := have - wanted:
        admin.call("DELETE", path, body=[rm_roles[n] for n in sorted(extra)], ok=(204,))


_COMPARED = (
    "enabled",
    "publicClient",
    "serviceAccountsEnabled",
    "standardFlowEnabled",
    "implicitFlowEnabled",
    "directAccessGrantsEnabled",
    "fullScopeAllowed",
    "clientAuthenticatorType",
)


def _check_settings(client_id, client, jwks):
    issues = []
    want = _client_rep(client_id, jwks or "")
    for key in _COMPARED:
        if client.get(key) != want[key]:
            issues.append(f"service account {client_id}: {key} is {client.get(key)!r}")
    attrs = client.get("attributes") or {}
    for key, value in want["attributes"].items():
        have = attrs.get(key) or ""
        if key == "jwks.string":
            if not jwks:
                issues.append(
                    f"service account {client_id}: no key set here to compare"
                )
            elif not same_keys(have, value):
                issues.append(f"service account {client_id}: keys differ from its set")
        elif have != value:
            issues.append(f"service account {client_id}: attribute {key} changed")
    if client.get("redirectUris") or client.get("webOrigins"):
        issues.append(f"service account {client_id} has redirect addresses")
    return issues


def check_service_accounts(admin, jwks_by_account):
    """Differences that would widen either account (``jwks_by_account`` as for ensure)."""
    issues = []
    rm_id, _ = _realm_management(admin)
    for client_id, wanted in ACCOUNTS.items():
        client = _find(admin, client_id)
        if client is None:
            issues.append(f"service account {client_id} is missing")
            continue
        issues += _check_settings(
            client_id, client, (jwks_by_account or {}).get(client_id)
        )
        cid = client["id"]
        sa = admin.get(f"clients/{cid}/service-account-user")["id"]
        held = {
            r["name"] for r in admin.get(f"users/{sa}/role-mappings/clients/{rm_id}")
        }
        if held != wanted:
            issues.append(
                f"service account {client_id} holds {sorted(held)}, want {sorted(wanted)}"
            )
        composite = admin.get(f"users/{sa}/role-mappings/clients/{rm_id}/composite")
        if {r["name"] for r in composite} - wanted - _IMPLIED:
            issues.append(f"service account {client_id} has wider roles via composites")
        if admin.get(f"users/{sa}/role-mappings/realm"):
            issues.append(f"service account {client_id} holds realm roles")
        others = set(admin.get(f"users/{sa}/role-mappings").get("clientMappings") or {})
        if others - {"realm-management"}:
            names = sorted(others - {"realm-management"})
            issues.append(f"service account {client_id} holds roles of {names}")
        if admin.get(f"users/{sa}/groups"):
            issues.append(f"service account {client_id} is in a group")
        scopes = {s["name"] for s in admin.get(f"clients/{cid}/default-client-scopes")}
        scopes |= {
            s["name"] for s in admin.get(f"clients/{cid}/optional-client-scopes")
        }
        if scopes != {ADMIN_API_SCOPE}:
            issues.append(
                f"service account {client_id} has client scopes {sorted(scopes)}"
            )
    return issues


def check_admin_holders(admin):
    """Nobody but the two workers may hold a realm-management role in this realm, directly,
    through a group, or through a realm role (default roles included): a worker's secret
    must never be able to leave a lasting human admin behind unnoticed. Reported, never
    removed by the sync: an operator decides."""
    issues = []
    rm_id, rm_roles = _realm_management(admin)
    allowed = set()
    for client_id in ACCOUNTS:
        if client := _find(admin, client_id):
            allowed.add(admin.get(f"clients/{client['id']}/service-account-user")["id"])
    for name in rm_roles:
        base = f"clients/{rm_id}/roles/{q(name)}"
        for user in admin.get(f"{base}/users?first=0&max=200"):
            if user["id"] not in allowed:
                issues.append(f"user {user['username']} holds realm-management {name}")
        if admin.get(f"{base}/groups?first=0&max=1"):
            issues.append(f"a group holds realm-management {name}")
    for role in admin.get("roles"):
        if role.get("composite") and admin.get(
            f"roles/{q(role['name'])}/composites/clients/{rm_id}"
        ):
            issues.append(f"realm role {role['name']} includes realm-management roles")
    return issues


# Roles Keycloak's own composites add to the ones we grant (view-users brings query-groups,
# view-clients and manage-clients nothing beyond the listed ones).
_IMPLIED = frozenset(("query-groups",))
