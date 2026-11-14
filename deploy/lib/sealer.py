"""Authenticated encryption in chunks, and authenticated records, shared by the daily log archive
(P1-T9c, ADR-014: deploy/logs/archive/) and the nightly per-hospital dumps (P1-T22a:
deploy/dumps/). Moved here from deploy/logs/archive/archive.py; the log archive's objects,
manifests and erasure records are byte for byte what they were.

An object is lines of Fernet tokens (AES-128-CBC + HMAC-SHA256, the `cryptography` package), each
holding one MiB of the plaintext. Each token is authenticated on its own; their order and number
are fixed by the SHA-256 of the whole object, which the object's manifest holds, and the manifest
is authenticated with a key derived from the same encryption key (HMAC-SHA256 over its canonical
JSON). Erasure records get a key of their own, also derived, so the owner's erasure tool can sign
them without ever holding the encryption key.

Each user names its purpose; the derived keys differ per purpose, so a log archive manifest can
never pass as a dump's, even under the same key:
    "ihr-log-archive"  the log archive (the labels it has always used)
    "ihr-dump"         the nightly dumps
Standard library and `cryptography` only.
"""

import base64
import hashlib
import hmac
import json
import re

CHUNK = 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def derived_key(key, label):
    raw = base64.urlsafe_b64decode(key)
    return hmac.new(raw, label.encode(), hashlib.sha256).digest()


def erasure_mac_key(key, purpose="ihr-log-archive"):
    """The key that authenticates erasure records (the owner's erasure tool gets this, never the
    encryption key)."""
    return derived_key(key, f"{purpose} erasure")


def body_mac(key, doc):
    body = {k: v for k, v in doc.items() if k != "mac"}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, canon, hashlib.sha256).hexdigest()


def mac_ok(key, doc):
    mac = doc.get("mac")
    return isinstance(mac, str) and bool(HEX64.match(mac)) and hmac.compare_digest(
        mac.encode(), body_mac(key, doc).encode())


def signed(key, doc):
    """doc with its MAC (an erasure record, a manifest)."""
    return {**doc, "mac": body_mac(key, doc)}


def record_bytes(doc):
    """How a manifest or record is stored (its SHA-256 is what the next one links to)."""
    return (json.dumps(doc, sort_keys=True, indent=1) + "\n").encode()


class Sealer:
    def __init__(self, key, purpose="ihr-log-archive"):
        from cryptography.fernet import Fernet

        self.fernet = Fernet(key.encode() if isinstance(key, str) else key)
        self.mac_key = derived_key(key, f"{purpose} manifest")
        self.erasure_key = erasure_mac_key(key, purpose)

    def encrypt_file(self, src, dst):
        self.seal(src, dst)

    def seal(self, src, dst):
        """src (a readable stream) encrypted into dst; returns the sizes and SHA-256 of both:
        {"plain_bytes", "plain_sha256", "bytes", "sha256"}."""
        plain, sealed = hashlib.sha256(), hashlib.sha256()
        n_plain = n_sealed = 0
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            # A pipe may return less than asked: fill the chunk, so the format stays one MiB a token
            while len(chunk) < CHUNK:
                more = src.read(CHUNK - len(chunk))
                if not more:
                    break
                chunk += more
            plain.update(chunk)
            n_plain += len(chunk)
            token = self.fernet.encrypt(chunk) + b"\n"
            sealed.update(token)
            n_sealed += len(token)
            dst.write(token)
        return {"plain_bytes": n_plain, "plain_sha256": plain.hexdigest(),
                "bytes": n_sealed, "sha256": sealed.hexdigest()}

    def decrypt_lines(self, src):
        """Yields the plaintext chunks of an object (a file of token lines)."""
        for token in src:
            token = token.strip()
            if token:
                yield self.fernet.decrypt(token)

    def mac(self, manifest):
        return body_mac(self.mac_key, manifest)
