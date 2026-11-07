"""Staff sessions held by the backend (ADR-009): the browser keeps only a random cookie.

Each session lives in Redis through Django's cache, so every key carries the hospital's schema
prefix (``django_tenants.cache.make_key``). The key is a hash of the cookie and the value is
encrypted with a key derived from the cookie: a Redis dump alone yields neither cookies nor
tokens. Activity time sits in a separate key, so marking activity can never overwrite tokens.
"""

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core.cache import cache

LOCK_SECONDS = 10


def _key(secret, purpose):
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=purpose.encode()
    ).derive(secret)


def seal(data, secret, purpose):
    """Encrypt ``data`` (JSON-able) with a key derived from ``secret`` (bytes)."""
    nonce = os.urandom(12)
    box = AESGCM(_key(secret, purpose)).encrypt(nonce, json.dumps(data).encode(), None)
    return base64.urlsafe_b64encode(nonce + box).decode()


def unseal(text, secret, purpose):
    """The data sealed by ``seal``, or None if it was altered or sealed with another key."""
    try:
        raw = base64.urlsafe_b64decode(text.encode())
        plain = AESGCM(_key(secret, purpose)).decrypt(raw[:12], raw[12:], None)
        return json.loads(plain)
    except (InvalidTag, ValueError, TypeError):
        return None


@dataclass
class StaffSession:
    tid: str  # schema of the hospital it was created in
    user_id: int
    sub: str
    sid: str
    created: float
    auth_time: float
    access: str
    access_exp: float
    access_iat: float
    refresh: str
    cookie: str = ""


def _base(cookie):
    return f"ihr:sess:{hashlib.sha256(cookie.encode()).hexdigest()}"


def _store(session):
    data = asdict(session)
    data.pop("cookie")
    cache.set(
        _base(session.cookie),
        seal(data, session.cookie.encode(), "ihr-session"),
        timeout=settings.IHR_SESSION_MAX_SECONDS,
    )


def create(**fields):
    session = StaffSession(
        cookie=secrets.token_urlsafe(32), created=time.time(), **fields
    )
    _store(session)
    cache.set(
        f"{_base(session.cookie)}:act",
        session.created,
        timeout=settings.IHR_SESSION_MAX_SECONDS,
    )
    return session


def load(cookie):
    if not cookie or len(cookie) > 100:  # noqa: PLR2004
        return None
    text = cache.get(_base(cookie))
    data = unseal(text, cookie.encode(), "ihr-session") if text else None
    return StaffSession(cookie=cookie, **data) if data else None


def save_tokens(session, tokens, claims):
    session.access = tokens["access_token"]
    session.refresh = tokens["refresh_token"]
    session.access_exp = claims["exp"]
    session.access_iat = claims["iat"]
    _store(session)


def last_activity(session):
    return cache.get(f"{_base(session.cookie)}:act") or session.created


def mark_activity(session, when):
    cache.set(
        f"{_base(session.cookie)}:act", when, timeout=settings.IHR_SESSION_MAX_SECONDS
    )


def lock(session):
    """A token if this request may refresh the tokens (one refresh per session at a time)."""
    token = secrets.token_hex(8)
    added = cache.add(f"{_base(session.cookie)}:lock", token, timeout=LOCK_SECONDS)
    return token if added else None


def unlock(session, token):
    key = f"{_base(session.cookie)}:lock"
    if cache.get(key) == token:  # never release a lock that expired and was re-taken
        cache.delete(key)


def _ended_key(sid):
    return f"ihr:sid-ended:{hashlib.sha256(sid.encode()).hexdigest()}"


def end_sign_in(sid):
    """Keycloak says sign-in ``sid`` ended (back-channel logout, P1-T5d): every session of
    it at this hospital is refused from the next request on. Kept as long as a session can
    live; the key is hospital-prefixed like every other."""
    cache.set(_ended_key(sid), time.time(), timeout=settings.IHR_SESSION_MAX_SECONDS)


def sign_in_ended(session):
    return cache.get(_ended_key(session.sid)) is not None


def delete(cookie):
    base = _base(cookie)
    cache.delete_many([base, f"{base}:act", f"{base}:lock"])
