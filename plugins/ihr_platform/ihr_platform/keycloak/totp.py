"""RFC 6238 one-time codes, matching Keycloak's TOTP (HmacSHA1, 6 digits, 30 s).

Keycloak keeps the secret as a plain string and uses its UTF-8 bytes as the HMAC key; the
authenticator-app key is the base32 form of those bytes.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

PERIOD = 30


def new_secret():
    """A random ASCII secret, 20 bytes when encoded (Keycloak's own length)."""
    return secrets.token_hex(10)


def app_key(raw_secret):
    """The key an authenticator app takes: base32 of the secret's bytes."""
    return base64.b32encode(raw_secret.encode()).decode().rstrip("=")


def code(raw_secret, at=None, step_offset=0):
    counter = int((time.time() if at is None else at) // PERIOD) + step_offset
    digest = hmac.new(
        raw_secret.encode(), struct.pack(">Q", counter), hashlib.sha1
    ).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def otpauth_uri(raw_secret, account, issuer):
    params = urlencode(
        {"secret": app_key(raw_secret), "issuer": issuer, "digits": 6, "period": PERIOD}
    )
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?{params}"
