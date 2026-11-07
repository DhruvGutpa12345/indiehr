"""The operator realm and its console client (P1-T5b, TENANCY_DESIGN §4.9).

Operators sign in to their own realm, never the hospitals' one, with the same baseline
(password policy, lock-out, MFA by level of authentication). The console client is public
with PKCE; the console's backend exchanges the code, like the hospitals' sign-in (ADR-009).
Tokens carry ``ihr-operator`` only through an explicit scope mapping (no full scope).
"""

from http import HTTPStatus

from .hospital import _ensure_mappers_for, _find_client
from .kc_http import q
from .realm import ensure_realm

OPERATOR_ROLE = "ihr-operator"
CONSOLE_CLIENT = "ihr-console"


def console_rep(redirect_uri, origin):
    return {
        "clientId": CONSOLE_CLIENT,
        "name": "IndieHR operator console",
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": True,
        "bearerOnly": False,
        "consentRequired": False,
        "standardFlowEnabled": True,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "authorizationServicesEnabled": False,
        "fullScopeAllowed": False,
        "frontchannelLogout": False,
        "rootUrl": origin,
        "baseUrl": "/ops/",
        "redirectUris": [redirect_uri],
        "webOrigins": [],
        "attributes": {
            "ihr.kind": "console",
            "pkce.code.challenge.method": "S256",
            "minimum.acr.value": "mfa",
            "oauth2.device.authorization.grant.enabled": "false",
            "oidc.ciba.grant.enabled": "false",
            "standard.token.exchange.enabled": "false",
            "use.refresh.tokens": "true",
            "post.logout.redirect.uris": f"{origin}/ops/signed-out/",
        },
        "defaultClientScopes": ["basic", "acr", "roles", "profile", "email"],
        "optionalClientScopes": [],
    }


def ensure_ops_realm(admin, redirect_uri, origin):
    """``admin`` manages the operator realm (its ``realm`` is the ops realm's name)."""
    ensure_realm(admin)
    if admin.call("GET", f"roles/{q(OPERATOR_ROLE)}", ok=(200, 404)).status == (
        HTTPStatus.NOT_FOUND
    ):
        admin.post(
            "roles",
            {"name": OPERATOR_ROLE, "description": "May use the operator console"},
        )
    rep = console_rep(redirect_uri, origin)
    client = _find_client(admin, CONSOLE_CLIENT)
    if client is None:
        resp = admin.post("clients", rep)
        client = admin.get(f"clients/{admin.created_id(resp)}")
    else:
        admin.put(f"clients/{client['id']}", rep | {"id": client["id"]})
    role = admin.get(f"roles/{q(OPERATOR_ROLE)}")
    # Exactly one scope mapping: the operator role. Anything else is removed.
    mappings = admin.get(f"clients/{client['id']}/scope-mappings")
    extra = [
        r for r in mappings.get("realmMappings") or [] if r["name"] != OPERATOR_ROLE
    ]
    if extra:
        admin.call("DELETE", f"clients/{client['id']}/scope-mappings/realm", body=extra)
    for other in (mappings.get("clientMappings") or {}).values():
        admin.call(
            "DELETE",
            f"clients/{client['id']}/scope-mappings/clients/{other['id']}",
            body=other["mappings"],
        )
    admin.post(f"clients/{client['id']}/scope-mappings/realm", [role])
    _ensure_mappers_for(
        admin,
        client["id"],
        {
            "ihr-audience": {
                "name": "ihr-audience",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-audience-mapper",
                "config": {
                    "included.client.audience": CONSOLE_CLIENT,
                    "access.token.claim": "true",
                    "id.token.claim": "false",
                    "introspection.token.claim": "true",
                },
            }
        },
    )
    return client["id"]


def check_ops_realm(admin, redirect_uri, origin):
    """Drift in the operator realm: its baseline, and the console client's settings."""
    from .realm import check_realm

    issues = [f"operator realm: {i}" for i in check_realm(admin, {CONSOLE_CLIENT})]
    client = _find_client(admin, CONSOLE_CLIENT)
    if client is None:
        return [*issues, "operator realm: console client is missing"]
    want = console_rep(redirect_uri, origin)
    for key in ("enabled", "publicClient", "directAccessGrantsEnabled", "implicitFlowEnabled",
                "serviceAccountsEnabled", "fullScopeAllowed", "redirectUris", "webOrigins"):  # fmt: skip
        if client.get(key) != want[key]:
            issues.append(
                f"operator realm: console client {key} is {client.get(key)!r}"
            )
    for key, value in want["attributes"].items():
        if client.get("attributes", {}).get(key) != value:
            issues.append(
                f"operator realm: console client attribute {key} is not {value!r}"
            )
    mappings = admin.get(f"clients/{client['id']}/scope-mappings")
    realm_roles = {r["name"] for r in mappings.get("realmMappings") or []}
    if realm_roles != {OPERATOR_ROLE} or mappings.get("clientMappings"):
        issues.append("operator realm: console client scope mappings changed")
    return issues
