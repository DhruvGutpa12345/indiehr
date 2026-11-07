"""One Keycloak organisation, login client and gate flow per hospital (Q-T4, ADR-008).

Hospital ``alpha`` gets:
  - organisation ``alpha`` (its staff are unmanaged members: one person, many hospitals);
  - public client ``ihr-h-alpha`` (PKCE, MFA, no full scope, no password grant);
  - client role ``ihr-h-alpha.member``, the only role Keycloak holds; job roles live in CARE;
  - flow ``ihr-hospital-alpha``, which refuses anyone without that role, on every login,
    including single sign-on from another hospital.
Tokens from that client carry ``azp=ihr-h-alpha``, ``resource_access.ihr-h-alpha.roles=["member"]``
and ``ihr_org={"alias": "alpha", "id": <organisation id>}``, set by the server, never by the request.
"""

import json
from dataclasses import dataclass
from http import HTTPStatus

from .kc_http import KeycloakError, q
from .realm import (
    CORE_SHAPE,
    FLOW_DENY,
    FLOW_TEMPLATE,
    GATE_SHAPE,
    HOSPITAL_DEFAULT_SCOPES,
    default_role_names,
    flow_shape,
    gate_execution,
    shape_matches,
)

MEMBER_ROLE = "member"
DEFAULT_SCOPES = HOSPITAL_DEFAULT_SCOPES
# Per-client overrides of the realm's token, session and MFA rules: always cleared.
OVERRIDE_ATTRIBUTES = (
    "access.token.lifespan",
    "client.session.idle.timeout",
    "client.session.max.lifespan",
    "client.offline.session.idle.timeout",
    "client.offline.session.max.lifespan",
    "acr.loa.map",
    "default.acr.values",
)


def client_id_for(slug):
    return f"ihr-h-{slug}"


def flow_for(slug):
    return f"ihr-hospital-{slug}"


@dataclass(frozen=True)
class HospitalLogin:
    slug: str
    name: str
    origins: tuple  # e.g. ("http://alpha.localhost:8080",)
    redirect_uris: tuple
    enabled: bool = True
    # Hospital.uuid, stamped on the organisation and client (P1-T5a): a leftover object under
    # the same alias (e.g. from a rolled-back hospital) is never adopted by a new hospital.
    uuid: str = ""
    # Where Keycloak posts logout tokens (P1-T5d): one internal address for all hospitals.
    backchannel_url: str = ""


@dataclass(frozen=True)
class HospitalIds:
    org_id: str
    client_uuid: str
    client_id: str
    flow_alias: str


def ensure_hospital(admin, h, org_id=None):
    """Create or correct hospital ``h``'s organisation, client, role and gate flow.

    A new client stays disabled until its gate flow is configured and bound, so it is never
    reachable through the realm flow. ``org_id`` is the id already stored for this hospital;
    a different organisation under the same alias is an error, never silently adopted.
    """
    fresh = org_id is None
    org_id = _ensure_org(admin, h, org_id)
    client = _find_client(admin, client_id_for(h.slug))
    if client is not None and fresh and h.uuid:
        _require_tag(client.get("attributes", {}).get(TAG), h, "client")
    if client is None:
        resp = admin.post(
            "clients", _client_rep(h, org_id, flow_ids=None, enabled=False)
        )
        client = admin.get(f"clients/{admin.created_id(resp)}")
    client_uuid = client["id"]
    _ensure_member_role(admin, client_uuid)
    flow_id = _ensure_flow(admin, h.slug)

    gate_issues = _check_gate_config(admin, h.slug)
    if gate_issues:
        # Leave the client as it was (a new one stays disabled) rather than bind a broken gate.
        raise KeycloakError("; ".join(gate_issues))
    deny_id = _flow_id(admin, FLOW_DENY)
    rep = _client_rep(
        h, org_id, {"browser": flow_id, "direct_grant": deny_id}, h.enabled
    )
    # An empty value removes a per-client override of the realm's rules.
    rep["attributes"] |= dict.fromkeys(OVERRIDE_ATTRIBUTES, "")
    admin.put(f"clients/{client_uuid}", rep | {"id": client_uuid})
    _clear_scope_mappings(admin, client_uuid)
    _ensure_scopes(admin, client_uuid)
    _ensure_mappers(admin, client_uuid, h, org_id)
    return HospitalIds(org_id, client_uuid, client_id_for(h.slug), flow_for(h.slug))


def check_hospital(admin, h, org_id):
    """Differences from the desired state that weaken the hospital's login."""
    issues = []
    org = admin.call("GET", f"organizations/{org_id}", ok=(200, 404))
    if org.status != HTTPStatus.OK or org.json().get("alias") != h.slug:
        issues.append(
            f"{h.slug}: organisation {org_id} is missing or has another alias"
        )
    client = _find_client(admin, client_id_for(h.slug))
    if client is None:
        return [*issues, f"{h.slug}: client {client_id_for(h.slug)} is missing"]
    issues += _check_client_settings(h, client, org_id)
    issues += _check_client_wiring(admin, h, client, org_id)
    issues += _check_gate_config(admin, h.slug)
    return issues


def _check_client_settings(h, client, org_id):
    issues = []
    want = _client_rep(h, org_id, None, h.enabled)
    for key in (
        "enabled",
        "publicClient",
        "standardFlowEnabled",
        "implicitFlowEnabled",
        "directAccessGrantsEnabled",
        "serviceAccountsEnabled",
        "fullScopeAllowed",
        "consentRequired",
        "frontchannelLogout",
    ):
        if client.get(key) != want[key]:
            issues.append(f"{h.slug}: client {key} is {client.get(key)!r}")
    for key, value in want["attributes"].items():
        # Keycloak drops an attribute set to "" (e.g. no back-channel address in tests)
        if (client.get("attributes", {}).get(key) or "") != value:
            issues.append(f"{h.slug}: client attribute {key} is not {value!r}")
    for key in OVERRIDE_ATTRIBUTES:
        if client.get("attributes", {}).get(key):
            issues.append(f"{h.slug}: client overrides the realm's {key}")
    for key in ("redirectUris", "webOrigins"):
        if sorted(client.get(key, [])) != sorted(want[key]):
            issues.append(f"{h.slug}: client {key} is {client.get(key)}")
    return issues


def _check_client_wiring(admin, h, client, org_id):
    """Flow bindings, scopes and token mappers."""
    issues = []
    overrides = client.get("authenticationFlowBindingOverrides", {})
    if overrides.get("browser") != _flow_id(admin, flow_for(h.slug)):
        issues.append(f"{h.slug}: client is not bound to flow {flow_for(h.slug)}")
    if overrides.get("direct_grant") != _flow_id(admin, FLOW_DENY):
        issues.append(f"{h.slug}: client direct-grant flow is not {FLOW_DENY}")
    scopes = {
        s["name"] for s in admin.get(f"clients/{client['id']}/default-client-scopes")
    }
    if scopes != set(DEFAULT_SCOPES):
        issues.append(f"{h.slug}: default scopes are {sorted(scopes)}")
    if admin.get(f"clients/{client['id']}/optional-client-scopes"):
        issues.append(
            f"{h.slug}: client has optional scopes (the request could add claims)"
        )
    mappers = {
        m["name"]: m
        for m in admin.get(f"clients/{client['id']}/protocol-mappers/models")
    }
    for name, rep in _mapper_reps(h, org_id).items():
        if name not in mappers or not _same_config(mappers[name], rep):
            issues.append(f"{h.slug}: token mapper {name} is missing or changed")
    if set(mappers) - set(_mapper_reps(h, org_id)):
        issues.append(
            f"{h.slug}: extra token mappers {sorted(set(mappers) - set(_mapper_reps(h, org_id)))}"
        )
    if flow_for(h.slug) in _flow_aliases(admin) and not shape_matches(
        flow_shape(admin, flow_for(h.slug)), CORE_SHAPE + GATE_SHAPE
    ):
        issues.append(
            f"{h.slug}: flow is not the core login plus its gate (run --rebuild-flows)"
        )
    mappings = admin.get(f"clients/{client['id']}/scope-mappings")
    if mappings.get("realmMappings") or mappings.get("clientMappings"):
        issues.append(f"{h.slug}: client has role scope mappings (could add roles)")
    issues += _check_member_role(admin, h.slug, client["id"])
    return issues


def _check_member_role(admin, slug, client_uuid):
    """``member`` must come only from a direct assignment, which remove_member undoes."""
    path = f"clients/{client_uuid}/roles/{MEMBER_ROLE}"
    role = admin.call("GET", path, ok=(200, 404))
    if role.status != HTTPStatus.OK:
        return []  # reported by _check_gate_config
    issues = []
    if role.json().get("composite"):
        issues.append(f"{slug}: role {MEMBER_ROLE} includes other roles")
    if admin.get(f"{path}/groups?first=0&max=1"):
        issues.append(f"{slug}: a group grants {MEMBER_ROLE}")
    if MEMBER_ROLE in default_role_names(admin, client_uuid):
        issues.append(f"{slug}: every user gets {MEMBER_ROLE} by default")
    return issues


def rebuild_flow(admin, h, org_id):
    """Recreate the hospital's flow from the current template. The client is disabled while
    its flow is replaced, so logins fail closed in between."""
    client = _find_client(admin, client_id_for(h.slug))
    if client:
        # Keycloak refuses to delete a flow a client is bound to: bind the deny-all flow
        # meanwhile (an empty override map would leave the old binding in place).
        deny_id = _flow_id(admin, FLOW_DENY)
        admin.put(
            f"clients/{client['id']}",
            dict(
                client,
                enabled=False,
                authenticationFlowBindingOverrides={
                    "browser": deny_id,
                    "direct_grant": deny_id,
                },
            ),
        )
    old = _flow_id(admin, flow_for(h.slug))
    if old:
        admin.delete(f"authentication/flows/{old}")
    return ensure_hospital(admin, h, org_id)


def ensure_member(admin, ids, user_id):
    """Add a person to a hospital: organisation membership plus the ``member`` role."""
    # The body is the bare user id as a JSON string; 409 means already a member.
    admin.call(
        "POST", f"organizations/{ids.org_id}/members", body=user_id, ok=(201, 204, 409)
    )
    role = admin.get(f"clients/{ids.client_uuid}/roles/{MEMBER_ROLE}")
    admin.post(f"users/{user_id}/role-mappings/clients/{ids.client_uuid}", [role])


def remove_member(admin, ids, user_id, *, end_sessions=True):
    """Remove a person from a hospital and end their sessions, so refresh tokens stop working
    at once (a refresh does not re-run the login flow). ``end_sessions=False`` when they never
    worked here (an invitation withdrawn, P1-T5e2): their other hospitals are left alone."""
    role = admin.get(f"clients/{ids.client_uuid}/roles/{MEMBER_ROLE}")
    admin.call(
        "DELETE",
        f"users/{user_id}/role-mappings/clients/{ids.client_uuid}",
        body=[role],
        ok=(204,),
    )
    admin.call("DELETE", f"organizations/{ids.org_id}/members/{user_id}", ok=(204, 404))
    if end_sessions:
        admin.post(f"users/{user_id}/logout", ok=(204,))
    effective = admin.get(
        f"users/{user_id}/role-mappings/clients/{ids.client_uuid}/composite"
    )
    if any(r["name"] == MEMBER_ROLE for r in effective):
        msg = (
            f"user {user_id} still holds {MEMBER_ROLE} through a group or another role"
        )
        raise KeycloakError(msg)


# --- helpers ------------------------------------------------------------------------------


def _ensure_org(admin, h, org_id):
    orgs = admin.get("organizations?briefRepresentation=true&first=0&max=10000")
    by_alias = {o["alias"]: o for o in orgs}
    found = by_alias.get(h.slug)
    if org_id and (found is None or found["id"] != org_id):
        msg = f"{h.slug}: stored organisation {org_id} does not match Keycloak"
        raise KeycloakError(msg)
    tags = {TAG: [h.uuid]} if h.uuid else {}
    if found is None:
        resp = admin.post(
            "organizations",
            {
                "name": _org_name(h),
                "alias": h.slug,
                "enabled": True,
                "attributes": tags,
            },
        )
        return admin.created_id(resp)
    full = admin.get(f"organizations/{found['id']}")
    if not org_id and h.uuid:
        # Nothing stored yet: only an organisation this very hospital created may be reused
        # (a resumed provisioning), never one left behind by another hospital.
        _require_tag((full.get("attributes") or {}).get(TAG), h, "organisation")
    attrs = dict(full.get("attributes") or {}, **tags)
    if (
        full.get("name") != _org_name(h)
        or not full.get("enabled", True)
        or attrs != (full.get("attributes") or {})
    ):
        admin.put(
            f"organizations/{found['id']}",
            dict(full, name=_org_name(h), enabled=True, attributes=attrs),
        )
    return found["id"]


TAG = "ihr.hospital_uuid"


def _require_tag(value, h, what):
    values = value if isinstance(value, list) else [value]
    if h.uuid not in values:
        msg = f"{h.slug}: an existing {what} belongs to another hospital; refusing to adopt it"
        raise KeycloakError(msg)


def remove_hospital(admin, h):
    """Delete a hospital's client, gate flow and organisation (roll-back of a hospital that
    never went live). Only objects stamped with this hospital's uuid are touched."""
    if not h.uuid:
        raise KeycloakError("Refusing to remove a hospital without its uuid.")
    client = _find_client(admin, client_id_for(h.slug))
    if client is not None:
        _require_tag(client.get("attributes", {}).get(TAG), h, "client")
        admin.delete(f"clients/{client['id']}")
    flow = _flow_id(admin, flow_for(h.slug))
    if flow:
        admin.delete(f"authentication/flows/{flow}")
    orgs = admin.get("organizations?briefRepresentation=true&first=0&max=10000")
    for org in orgs:
        if org["alias"] == h.slug:
            full = admin.get(f"organizations/{org['id']}")
            _require_tag((full.get("attributes") or {}).get(TAG), h, "organisation")
            admin.delete(f"organizations/{org['id']}")


def _org_name(h):
    """Organisation names are unique in Keycloak; hospital names need not be."""
    return f"{h.name} ({h.slug})"


def _find_client(admin, client_id):
    found = admin.get(f"clients?clientId={q(client_id)}")
    return found[0] if found else None


def _client_rep(h, org_id, flow_ids, enabled):
    rep = {
        "clientId": client_id_for(h.slug),
        "name": h.name,
        "description": f"Staff login for {h.name}",
        "enabled": enabled,
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
        # Sign-outs reach the backend server to server, never through the browser (P1-T5d)
        "frontchannelLogout": False,
        "rootUrl": h.origins[0],
        "baseUrl": "/",
        "redirectUris": list(h.redirect_uris),
        "webOrigins": list(h.origins),
        "attributes": {
            "ihr.kind": "hospital",
            "ihr.tenant": h.slug,
            **({TAG: h.uuid} if h.uuid else {}),
            "pkce.code.challenge.method": "S256",
            "minimum.acr.value": "mfa",
            "oauth2.device.authorization.grant.enabled": "false",
            "oidc.ciba.grant.enabled": "false",
            "standard.token.exchange.enabled": "false",
            "post.logout.redirect.uris": "##".join(f"{o}/" for o in h.origins),
            "use.refresh.tokens": "true",
            "backchannel.logout.url": h.backchannel_url,
            "backchannel.logout.session.required": "true",
            "backchannel.logout.revoke.offline.tokens": "false",
        },
        "defaultClientScopes": list(DEFAULT_SCOPES),
        "optionalClientScopes": [],
    }
    if flow_ids:
        rep["authenticationFlowBindingOverrides"] = flow_ids
    else:
        rep["authenticationFlowBindingOverrides"] = {}
    if flow_ids is None:
        rep["protocolMappers"] = list(_mapper_reps(h, org_id).values())
    return rep


def _mapper_reps(h, org_id):
    return {
        "ihr-audience": {
            "name": "ihr-audience",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper",
            "config": {
                "included.client.audience": client_id_for(h.slug),
                "access.token.claim": "true",
                "id.token.claim": "false",
                "introspection.token.claim": "true",
            },
        },
        "ihr-org": {
            "name": "ihr-org",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-hardcoded-claim-mapper",
            "config": {
                "claim.name": "ihr_org",
                "claim.value": json.dumps({"alias": h.slug, "id": org_id}),
                "jsonType.label": "JSON",
                "access.token.claim": "true",
                "id.token.claim": "true",
                "userinfo.token.claim": "false",
                "introspection.token.claim": "true",
            },
        },
    }


def _ensure_member_role(admin, client_uuid):
    status = admin.call(
        "GET", f"clients/{client_uuid}/roles/{MEMBER_ROLE}", ok=(200, 404)
    ).status
    if status == HTTPStatus.NOT_FOUND:
        admin.post(
            f"clients/{client_uuid}/roles",
            {"name": MEMBER_ROLE, "description": "May sign in to this hospital"},
        )


def _flow_aliases(admin):
    return {f["alias"]: f["id"] for f in admin.get("authentication/flows")}


def _flow_id(admin, alias):
    return _flow_aliases(admin).get(alias)


def _ensure_flow(admin, slug):
    alias = flow_for(slug)
    if alias not in _flow_aliases(admin):
        admin.post(f"authentication/flows/{q(FLOW_TEMPLATE)}/copy", {"newName": alias})
    gate, config, _ = gate_execution(admin, alias, "conditional-user-role")
    want = {"condUserRole": f"{client_id_for(slug)}.{MEMBER_ROLE}", "negate": "true"}
    if config is None:
        admin.post(
            f"authentication/executions/{gate['id']}/config",
            {"alias": f"{alias}-member", "config": want},
        )
    elif config["config"] != want:
        admin.put(f"authentication/config/{config['id']}", dict(config, config=want))
    return _flow_id(admin, alias)


def _check_gate_config(admin, slug):
    """The gate must name this hospital's own ``member`` role, and that role must exist:
    Keycloak's role condition skips the deny step when the role cannot be found."""
    alias = flow_for(slug)
    if alias not in _flow_aliases(admin):
        return [f"{slug}: flow {alias} is missing"]
    try:
        gate, config, execs = gate_execution(admin, alias, "conditional-user-role")
    except KeycloakError as exc:
        return [f"{slug}: {exc}"]
    issues = []
    want = {"condUserRole": f"{client_id_for(slug)}.{MEMBER_ROLE}", "negate": "true"}
    if not config or config["config"] != want:
        issues.append(f"{slug}: gate does not require {want['condUserRole']}")
    client = _find_client(admin, client_id_for(slug))
    if (
        client is None
        or admin.call(
            "GET", f"clients/{client['id']}/roles/{MEMBER_ROLE}", ok=(200, 404)
        ).status
        != HTTPStatus.OK
    ):
        issues.append(
            f"{slug}: role {client_id_for(slug)}.{MEMBER_ROLE} is missing (gate would let everyone in)"
        )
    deny = [e for e in execs if e.get("providerId") == "deny-access-authenticator"]
    if (
        len(deny) != 1
        or deny[0]["requirement"] != "REQUIRED"
        or gate["requirement"] != "REQUIRED"
    ):
        issues.append(f"{slug}: gate steps are not REQUIRED")
    return issues


def _same_config(have, want):
    """Keycloak adds its own defaults to a mapper's config: compare the keys we set."""
    return have.get("protocolMapper") == want["protocolMapper"] and all(
        have["config"].get(k) == v for k, v in want["config"].items()
    )


def _ensure_scopes(admin, client_uuid):
    scope_ids = {s["name"]: s["id"] for s in admin.get("client-scopes")}
    have = {
        s["name"] for s in admin.get(f"clients/{client_uuid}/default-client-scopes")
    }
    for name in set(DEFAULT_SCOPES) - have:
        admin.put(f"clients/{client_uuid}/default-client-scopes/{scope_ids[name]}")
    for name in have - set(DEFAULT_SCOPES):
        admin.delete(f"clients/{client_uuid}/default-client-scopes/{scope_ids[name]}")
    for scope in admin.get(f"clients/{client_uuid}/optional-client-scopes"):
        admin.delete(f"clients/{client_uuid}/optional-client-scopes/{scope['id']}")


def _clear_scope_mappings(admin, client_uuid):
    mappings = admin.get(f"clients/{client_uuid}/scope-mappings")
    if mappings.get("realmMappings"):
        admin.call(
            "DELETE",
            f"clients/{client_uuid}/scope-mappings/realm",
            body=mappings["realmMappings"],
        )
    for other in (mappings.get("clientMappings") or {}).values():
        admin.call(
            "DELETE",
            f"clients/{client_uuid}/scope-mappings/clients/{other['id']}",
            body=other["mappings"],
        )


def _ensure_mappers(admin, client_uuid, h, org_id):
    _ensure_mappers_for(admin, client_uuid, _mapper_reps(h, org_id))


def _ensure_mappers_for(admin, client_uuid, want):
    """Exactly these token mappers on the client (extra ones are removed)."""
    have = {
        m["name"]: m
        for m in admin.get(f"clients/{client_uuid}/protocol-mappers/models")
    }
    for name, mapper in have.items():
        if name not in want:
            admin.delete(
                f"clients/{client_uuid}/protocol-mappers/models/{mapper['id']}"
            )
    for name, rep in want.items():
        if name not in have:
            admin.post(f"clients/{client_uuid}/protocol-mappers/models", rep)
        elif not _same_config(have[name], rep):
            admin.put(
                f"clients/{client_uuid}/protocol-mappers/models/{have[name]['id']}",
                dict(have[name], config={**have[name]["config"], **rep["config"]}),
            )


def hospital_ids(admin, slug, org_id):
    """Ids of an already provisioned hospital."""
    client = _find_client(admin, client_id_for(slug))
    if client is None:
        msg = f"{slug}: client {client_id_for(slug)} is missing; run ihr_keycloak_sync"
        raise KeycloakError(msg)
    return HospitalIds(org_id, client["id"], client_id_for(slug), flow_for(slug))
