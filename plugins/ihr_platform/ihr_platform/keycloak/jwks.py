"""Public key sets for client authentication (P1-T8d2, ADR-012), standard library only.

What Keycloak holds in a client's ``jwks.string``: EC P-256 public keys for ES256 signatures,
each with ``"use": "sig"`` (Keycloak silently skips a key without it). The signing side, with
the private keys, is ``ihr_platform.client_keys``.
"""

import json

ALG = "ES256"


class ClientKeyError(Exception):
    """A key folder or public key set that cannot be used. Messages name files and key ids
    only, never key material."""


def canonical(keys):
    """One fixed text for a key set (sorted by kid, sorted members, no spaces), so the same
    set is always written the same way."""
    ordered = sorted(keys, key=lambda k: k["kid"])
    return json.dumps({"keys": ordered}, sort_keys=True, separators=(",", ":"))


def parse_jwks(text):
    """The keys of a published set, checked: EC P-256 public keys for ES256 signatures only.
    Anything else, a private member included, is refused."""
    try:
        data = json.loads(text)
        keys = data["keys"]
    except (TypeError, ValueError, KeyError) as exc:
        msg = "not a JSON key set"
        raise ClientKeyError(msg) from exc
    if not isinstance(keys, list) or not keys:
        msg = "the key set is empty"
        raise ClientKeyError(msg)
    want = {"kty": "EC", "crv": "P-256", "use": "sig", "alg": ALG}
    for key in keys:
        if not isinstance(key, dict) or set(key) != {*want, "x", "y", "kid"}:
            msg = "a key in the set has unexpected members (private parts are refused)"
            raise ClientKeyError(msg)
        if any(key[k] != v for k, v in want.items()) or not key["kid"]:
            msg = f"key {key.get('kid')!r} is not an ES256 signing key"
            raise ClientKeyError(msg)
    return keys


def same_keys(published, wanted):
    """True when two key sets hold exactly the same keys, however they are written."""
    try:
        have, want = parse_jwks(published), parse_jwks(wanted)
    except ClientKeyError:
        return False

    def dump(keys):
        return {json.dumps(k, sort_keys=True) for k in keys}

    return dump(have) == dump(want)
