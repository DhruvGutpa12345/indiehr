"""Checks on a hospital's Keycloak tokens (ADR-008, ADR-009). No Django imports.

Every check fails closed and each sits on its own line (scripts/isolation-mutation.sh targets
some of them). The expected values come from the current hospital, never from the request.
"""

import jwt

ALGORITHMS = ["RS256"]
MAX_ACCESS_LIFETIME = 600  # seconds; ADR-008 sets 300, anything longer means drift
ACCESS_CLAIMS = ["exp", "iat", "iss", "aud", "azp", "sub", "typ", "acr", "sid"]
ID_CLAIMS = ["exp", "iat", "iss", "aud", "azp", "sub", "typ", "nonce", "auth_time"]
# Back-channel logout (P1-T5d, OIDC Back-Channel Logout 1.0 §2.4); Keycloak also sends exp.
LOGOUT_CLAIMS = ["exp", "iat", "iss", "aud", "jti", "events", "sid"]
LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
LOGOUT_TYPE = "logout+jwt"
MAX_LOGOUT_AGE = 300  # seconds; Keycloak's logout tokens live 120


class TokenRejectedError(Exception):
    pass


class TokenExpiredError(TokenRejectedError):
    """Valid apart from its age: a refresh may still replace it."""


def _decode(token, keys, issuer, required, now, leeway):
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenRejectedError("malformed token") from exc
    if header.get("alg") not in ALGORITHMS:
        msg = f"algorithm {header.get('alg')!r} refused"
        raise TokenRejectedError(msg)
    key = keys.get(header.get("kid"))
    if key is None:
        raise TokenRejectedError("unknown signing key")
    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=ALGORITHMS,
            leeway=leeway,
            options={"require": required, "verify_aud": False, "verify_iat": False},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError("token expired") from exc
    except jwt.PyJWTError as exc:
        msg = f"invalid token: {exc}"
        raise TokenRejectedError(msg) from exc
    if claims["iss"] != issuer:
        raise TokenRejectedError("wrong issuer")
    if now is not None and claims["iat"] > now + leeway:
        raise TokenRejectedError("issued in the future")
    return claims


def _audiences(claims):
    aud = claims["aud"]
    return [aud] if isinstance(aud, str) else list(aud)


def verify_access_token(
    token, *, keys, issuer, client_id, org_id, slug, now=None, leeway=30
):
    """The claims of a valid access token for hospital ``slug``, or TokenRejectedError."""
    claims = _decode(token, keys, issuer, ACCESS_CLAIMS, now, leeway)
    if claims["typ"] != "Bearer":
        raise TokenRejectedError("not an access token")
    if claims["exp"] - claims["iat"] > MAX_ACCESS_LIFETIME:
        raise TokenRejectedError("lifetime longer than allowed")
    if claims["azp"] != client_id:
        raise TokenRejectedError("issued to another hospital's client")
    if _audiences(claims) != [client_id]:
        raise TokenRejectedError("audience is not exactly this hospital")
    access = claims.get("resource_access") or {}
    if set(access) != {client_id}:
        raise TokenRejectedError("roles of other clients in the token")
    if "member" not in (access[client_id].get("roles") or []):
        raise TokenRejectedError("not a member of this hospital")
    org = claims.get("ihr_org") or {}
    if org.get("id") != str(org_id) or org.get("alias") != slug:
        raise TokenRejectedError("issued for another organisation")
    if claims["acr"] != "mfa":
        raise TokenRejectedError("signed in without the second factor")
    return claims


def verify_operator_token(token, *, keys, issuer, client_id, role, now=None, leeway=30):
    """The claims of an operator's access token for the console (P1-T5b): issued by the
    operator realm to the console client, with MFA and the operator role."""
    claims = _decode(token, keys, issuer, ACCESS_CLAIMS, now, leeway)
    if claims["typ"] != "Bearer":
        raise TokenRejectedError("not an access token")
    if claims["exp"] - claims["iat"] > MAX_ACCESS_LIFETIME:
        raise TokenRejectedError("lifetime longer than allowed")
    if claims["azp"] != client_id or _audiences(claims) != [client_id]:
        raise TokenRejectedError("issued to another client")
    if role not in ((claims.get("realm_access") or {}).get("roles") or []):
        raise TokenRejectedError("not an operator")
    if claims.get("resource_access"):
        raise TokenRejectedError("roles of other clients in the token")
    if (
        claims.get("acr") != "mfa"
    ):  # (not the hospitals' line: a mutation targets that one)
        raise TokenRejectedError("signed in without the second factor")
    return claims


def verify_logout_token(token, *, keys, issuer, client_id, now, leeway=30):
    """The claims of a logout token Keycloak sent this hospital's back-channel, or
    TokenRejectedError. It must name the sign-in (``sid``) that ended."""
    try:
        header_type = jwt.get_unverified_header(token).get("typ")
    except jwt.PyJWTError as exc:
        raise TokenRejectedError("malformed token") from exc
    if header_type != LOGOUT_TYPE:
        raise TokenRejectedError("not a logout token")
    claims = _decode(token, keys, issuer, LOGOUT_CLAIMS, now, leeway)
    if _audiences(claims) != [client_id]:
        raise TokenRejectedError("logout token for another client")
    if LOGOUT_EVENT not in (claims["events"] or {}):
        raise TokenRejectedError("no back-channel logout event")
    if "nonce" in claims:
        raise TokenRejectedError("a logout token never carries a nonce")
    if not isinstance(claims["sid"], str) or not claims["sid"]:
        raise TokenRejectedError("logout token names no sign-in")
    if now - claims["iat"] > MAX_LOGOUT_AGE + leeway:
        raise TokenRejectedError("logout token too old")
    return claims


def verify_id_token(
    token, *, keys, issuer, client_id, nonce, access, now=None, leeway=30
):
    """The claims of the sign-in's ID token, matched to its access token."""
    claims = _decode(token, keys, issuer, ID_CLAIMS, now, leeway)
    if claims["typ"] != "ID":
        raise TokenRejectedError("not an ID token")
    if claims["azp"] != client_id or _audiences(claims) != [client_id]:
        raise TokenRejectedError("ID token for another client")
    if claims["nonce"] != nonce:
        raise TokenRejectedError("nonce mismatch")
    if claims["sub"] != access["sub"] or claims.get("sid") != access["sid"]:
        raise TokenRejectedError("ID and access tokens belong to different sign-ins")
    return claims
