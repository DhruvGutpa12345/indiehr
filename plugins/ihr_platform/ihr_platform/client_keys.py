"""The keys our own processes sign in to Keycloak with (P1-T8d2, ADR-012).

Instead of a shared client secret, each process proves who it is with a short JWT signed by its
own private key ("private_key_jwt", RFC 7523). Three keys, one per process role:

    backend      signs for every hospital client ihr-h-<slug> and for the console ihr-console
    onboarding   signs for ihr-onboarding (the provisioner)
    staff        signs for ihr-staff-worker

Private keys live only on the server, each in its own Docker volume that only its own container
mounts, read-only (owner, Q-K17). They are never backed up: a lost key is replaced by a new one.
Keycloak holds only the public halves, in each client's ``jwks.string`` (ihr_keycloak_sync).

A key folder holds ``state.json`` and one ``<kid>.pem`` per key, all mode 600. A key is
``staged`` (published, not yet used), ``active`` (signs), ``previous`` (published until no
assertion it signed can still be in flight) or ``retired`` (no longer published). Signers re-read
``state.json`` when it changes, so a rotation needs no restart.

Standard library, cryptography and PyJWT only, never Django: the key tool runs without settings.

    python -m ihr_platform.client_keys <command> --purpose P [--dir /run/ihr-keys] [--kid K]

    ensure     make an active key if the folder has none     prints its kid
    stage      a new key, published but not used yet         prints its kid
    activate   sign with the staged key (or --kid)           prints its kid
    retire     stop publishing previous keys (or --kid)      prints the kids retired
    delete     remove retired keys for good (or --kid)       prints the kids deleted
    public     the public key set to publish                 prints it as JSON
    status     every key: kid, state, created (IST), age in days, one key per line
               (--json: also the verdict on the active key's age)

Errors go to stderr with exit status 2. Nothing here prints or logs a private key or an
assertion.
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

# The parts the Keycloak code needs too, kept standard library only there (keycloak/jwks.py).
from ihr_platform.keycloak.jwks import (
    ALG,
    ClientKeyError,
    canonical,
    parse_jwks,
    same_keys,
)

__all__ = [
    "ALG",
    "ClientKeyError",
    "KeyStore",
    "canonical",
    "make_assertion",
    "parse_jwks",
    "read_public_jwks",
    "same_keys",
]

PURPOSES = ("backend", "onboarding", "staff")
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
# Keycloak refuses an assertion older than max.exp (60 s, set on every client) and gives exp no
# clock skew; 30 s leaves room for a slow request and a little clock drift.
LIFETIME = 30
MAX_LIFETIME = 60
# Owner, Q-K18: rotate by hand every 180 days; smoke warns from 150 days and fails from 365.
WARN_DAYS = 150
FAIL_DAYS = 365
DEFAULT_DIR = "/run/ihr-keys"
STATE_FILE = "state.json"
STAGED, ACTIVE, PREVIOUS, RETIRED = "staged", "active", "previous", "retired"
STATES = (STAGED, ACTIVE, PREVIOUS, RETIRED)
PUBLISHED = (STAGED, ACTIVE, PREVIOUS)
IST = timezone(timedelta(hours=5, minutes=30), "IST")
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_THUMBPRINT_CHARS = 16


# --- public keys ------------------------------------------------------------------------


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _now():
    return int(time.time())


def public_jwk(public_key, kid):
    """The public half as Keycloak reads it from ``jwks.string``. ``use`` is required there:
    Keycloak silently skips a key without it (JWKSUtils, 26.8)."""
    numbers = public_key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64(numbers.x.to_bytes(32, "big")),
        "y": _b64(numbers.y.to_bytes(32, "big")),
        "kid": kid,
        "use": "sig",
        "alg": ALG,
    }


def thumbprint(jwk):
    """RFC 7638 thumbprint of an EC key (SHA-256, base64url)."""
    members = {k: jwk[k] for k in ("crv", "kty", "x", "y")}
    digest = hashlib.sha256(
        json.dumps(members, sort_keys=True, separators=(",", ":")).encode()
    ).digest()
    return _b64(digest)


def kid_for(purpose, public_key):
    """``<purpose>-<first 16 characters of the thumbprint>``: a new key always gets a new kid
    (Keycloak caches keys by kid), and the purpose stays readable in Keycloak's events."""
    return f"{purpose}-{thumbprint(public_jwk(public_key, ''))[:_THUMBPRINT_CHARS]}"


def read_public_jwks(directory, purpose):
    """``<directory>/<purpose>.jwks.json``, as written by the key tool's ``public``, checked
    and in canonical form. Read on every call: a long-running process sees a new set."""
    path = Path(directory) / f"{purpose}.jwks.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = (
            f"no public key set for {purpose} at {path} (run the key tool: keys ensure)"
        )
        raise ClientKeyError(msg) from exc
    try:
        keys = parse_jwks(text)
    except ClientKeyError as exc:
        msg = f"{path}: {exc}"
        raise ClientKeyError(msg) from exc
    if any(not k["kid"].startswith(f"{purpose}-") for k in keys):
        msg = f"{path} holds a key of another purpose"
        raise ClientKeyError(msg)
    return canonical(keys)


# --- assertions -------------------------------------------------------------------------


def make_assertion(
    key,
    kid,
    client_id,
    audience,
    *,
    now=None,
    lifetime=LIFETIME,
    jti=None,
    algorithm=ALG,
):
    """A client assertion (RFC 7523): iss = sub = the client, aud = the realm's public issuer
    (the only audience Keycloak accepts for token, refresh and logout alike), a lifetime of
    ``lifetime`` seconds and a single-use jti. Keycloak keys its replay store on the jti alone,
    with no realm or client, so the jti is globally unique."""
    iat = _now() if now is None else int(now)
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": audience,
        "iat": iat,
        "exp": iat + lifetime,
        "jti": jti or secrets.token_urlsafe(24),
    }
    return jwt.encode(
        claims, key, algorithm=algorithm, headers={"kid": kid, "typ": "JWT"}
    )


def assertion_fields(assertion):
    return {"client_assertion_type": ASSERTION_TYPE, "client_assertion": assertion}


def new_private_key():
    return ec.generate_private_key(ec.SECP256R1())


# --- the key folder ---------------------------------------------------------------------


def _created(entry):
    return datetime.strptime(entry["created"], _TIME_FORMAT).replace(tzinfo=UTC)


def _check_mode(path, st):
    if st.st_mode & 0o077:
        msg = (
            f"{path} has mode {oct(st.st_mode & 0o777)}: other users could read it; "
            "it must be 600"
        )
        raise ClientKeyError(msg)


def _is_p256(private):
    """An EC private key on the P-256 curve (ES256)."""
    if not isinstance(private, ec.EllipticCurvePrivateKey):
        return False
    return isinstance(private.curve, ec.SECP256R1)


class KeyStore:
    """One process role's key folder. Safe to share between threads; reads are cheap: one
    ``stat`` of ``state.json`` per signature, a reload only when it changed."""

    def __init__(self, directory=DEFAULT_DIR):
        self.dir = Path(directory)
        self._lock = threading.RLock()
        self._seen = None  # (inode, mtime_ns, size) of the state.json last read
        self._state = None
        self._private = {}  # kid -> private key (checked against its kid)

    # reading --------------------------------------------------------------------------

    def _path(self, name):
        return self.dir / name

    def state(self):
        """The folder's state, or None when the folder holds no key yet."""
        path = self._path(STATE_FILE)
        with self._lock:
            try:
                st = path.stat()
            except FileNotFoundError:
                self._seen, self._state = None, None
                return None
            except OSError as exc:
                msg = f"cannot read {path}: {exc.strerror}"
                raise ClientKeyError(msg) from exc
            seen = (st.st_ino, st.st_mtime_ns, st.st_size)
            if seen != self._seen:
                _check_mode(path, st)
                try:
                    with path.open(encoding="utf-8") as handle:
                        state = json.load(handle)
                except (OSError, ValueError) as exc:
                    msg = f"cannot read {path}"
                    raise ClientKeyError(msg) from exc
                self._validate(state)
                self._state, self._seen = state, seen
                kids = {k["kid"] for k in state["keys"]}
                self._private = {k: v for k, v in self._private.items() if k in kids}
            return json.loads(json.dumps(self._state))

    def _validate(self, state):
        path = self._path(STATE_FILE)
        if not isinstance(state, dict) or state.get("purpose") not in PURPOSES:
            msg = f"{path} names no known purpose"
            raise ClientKeyError(msg)
        keys = state.get("keys")
        if not isinstance(keys, list):
            msg = f"{path} has no key list"
            raise ClientKeyError(msg)
        for entry in keys:
            if entry.get("state") not in STATES or not str(
                entry.get("kid", "")
            ).startswith(f"{state['purpose']}-"):
                msg = f"{path} has a malformed entry"
                raise ClientKeyError(msg)
        if sum(1 for k in keys if k["state"] == ACTIVE) > 1:
            msg = f"{path} has more than one active key"
            raise ClientKeyError(msg)

    def purpose(self):
        state = self.state()
        return state["purpose"] if state else None

    def _require_state(self):
        state = self.state()
        if state is None:
            msg = f"no client key in {self.dir} (run the key tool: keys ensure)"
            raise ClientKeyError(msg)
        return state

    def kids(self, which):
        return [k["kid"] for k in self._require_state()["keys"] if k["state"] == which]

    def active_kid(self):
        active = self.kids(ACTIVE)
        if not active:
            msg = f"no active client key in {self.dir}"
            raise ClientKeyError(msg)
        return active[0]

    def private_key(self, kid):
        """The private key ``kid``; refused unless its file is mode 600 and its public half
        matches the kid (a swapped file is caught)."""
        state = self._require_state()
        if kid not in {k["kid"] for k in state["keys"]}:
            msg = f"no key {kid} in {self.dir}"
            raise ClientKeyError(msg)
        with self._lock:
            if kid in self._private:
                return self._private[kid]
            path = self._path(f"{kid}.pem")
            try:
                with path.open("rb") as handle:
                    # The mode of the file actually opened, not of a name checked before.
                    _check_mode(path, os.fstat(handle.fileno()))
                    data = handle.read()
            except OSError as exc:
                msg = f"cannot read {path}: {exc.strerror}"
                raise ClientKeyError(msg) from exc
            try:
                key = serialization.load_pem_private_key(data, password=None)
            except ValueError as exc:
                msg = f"{path} is not a private key"
                raise ClientKeyError(msg) from exc
            if not _is_p256(key):
                msg = f"{path} is not a P-256 key"
                raise ClientKeyError(msg)
            if kid_for(state["purpose"], key.public_key()) != kid:
                msg = f"{path} does not hold key {kid}"
                raise ClientKeyError(msg)
            self._private[kid] = key
            return key

    def check(self):
        """The active key's id, after loading it: fails if the process could not sign."""
        kid = self.active_kid()
        self.private_key(kid)
        return kid

    def sign(self, client_id, audience, kid=None):
        """A fresh assertion for ``client_id``, signed with the active key (or ``kid``)."""
        kid = kid or self.active_kid()
        return make_assertion(self.private_key(kid), kid, client_id, audience)

    def fields(self, client_id, audience, kid=None):
        """The form fields that authenticate ``client_id`` at Keycloak's token endpoint."""
        return assertion_fields(self.sign(client_id, audience, kid))

    def public_keys(self):
        state = self._require_state()
        return [
            public_jwk(self.private_key(k["kid"]).public_key(), k["kid"])
            for k in state["keys"]
            if k["state"] in PUBLISHED
        ]

    def public_jwks(self):
        """The set Keycloak must hold: staged, active and previous keys; never retired ones,
        never a private member."""
        return canonical(self.public_keys())

    def status(self, now=None):
        """Every key with its age, and a verdict on the active key's age (Q-K18)."""
        state = self._require_state()
        now = datetime.fromtimestamp(_now() if now is None else now, UTC)
        keys = []
        for entry in state["keys"]:
            created = _created(entry)
            keys.append(
                {
                    "kid": entry["kid"],
                    "state": entry["state"],
                    "created": entry["created"],
                    "created_ist": created.astimezone(IST).strftime(
                        "%d-%m-%Y %H:%M IST"
                    ),
                    "age_days": (now - created).days,
                }
            )
        active = [k for k in keys if k["state"] == ACTIVE]
        purpose = state["purpose"]
        rotate = f"rotate it (keys rotate {purpose})"
        if not active:
            verdict, message = "FAIL", f"no active {purpose} key"
        else:
            age = active[0]["age_days"]
            message = f"active {purpose} key {active[0]['kid']} is {age} days old"
            if age >= FAIL_DAYS:
                verdict, message = "FAIL", f"{message}: {rotate} now"
            elif age >= WARN_DAYS:
                verdict, message = (
                    "WARN",
                    f"{message}: {rotate} before {FAIL_DAYS} days",
                )
            else:
                verdict = "ok"
        return {
            "purpose": purpose,
            "keys": keys,
            "verdict": verdict,
            "message": message,
        }

    # changing (the key tool only) -------------------------------------------------------

    def _write(self, name, data):
        """Write a file atomically, mode 600: readers see the old or the new file, never half."""
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".tmp-")  # mkstemp: mode 600
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            Path(tmp).replace(self._path(name))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        dir_fd = os.open(self.dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _save(self, state):
        self._validate(state)
        self._write(STATE_FILE, json.dumps(state, indent=1).encode())
        self._seen = None  # read it back on next use

    def _new_key(self, purpose, which):
        key = new_private_key()
        kid = kid_for(purpose, key.public_key())
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        self._write(
            f"{kid}.pem", pem
        )  # the key first: state.json never names a missing one
        entry = {
            "kid": kid,
            "state": which,
            "created": datetime.now(UTC).strftime(_TIME_FORMAT),
        }
        return kid, entry

    def ensure(self, purpose):
        """An active key for ``purpose``, made if the folder has none. Returns (kid, made)."""
        if purpose not in PURPOSES:
            msg = f"unknown purpose {purpose!r}"
            raise ClientKeyError(msg)
        with self._lock:
            if not self.dir.is_dir():
                self.dir.mkdir(mode=0o700, parents=True)
            state = self.state()
            if state is None:
                state = {"purpose": purpose, "keys": []}
            elif state["purpose"] != purpose:
                msg = f"{self.dir} holds the {state['purpose']} key, not {purpose}"
                raise ClientKeyError(msg)
            active = [k["kid"] for k in state["keys"] if k["state"] == ACTIVE]
            if active:
                self.private_key(active[0])
                return active[0], False
            kid, entry = self._new_key(purpose, ACTIVE)
            state["keys"].append(entry)
            self._save(state)
            return kid, True

    def stage(self, *, fresh=False):
        """A new key, published but not yet used. Running it again returns the same staged
        key, so a rotation that stopped half-way can simply be run again. ``fresh`` (suspected
        compromise) discards any staged key first: it sat in the same folder as the stolen one,
        so it may be known to the thief too."""
        with self._lock:
            state = self._require_state()
            staged = [k["kid"] for k in state["keys"] if k["state"] == STAGED]
            if staged and not fresh:
                return staged[0]
            if staged:
                state["keys"] = [k for k in state["keys"] if k["state"] != STAGED]
                self._save(state)  # first: state.json never names a missing file
                for name in staged:
                    self._path(f"{name}.pem").unlink(missing_ok=True)
                    self._private.pop(name, None)
            kid, entry = self._new_key(state["purpose"], STAGED)
            state["keys"].append(entry)
            self._save(state)
            return kid

    def activate(self, kid=None, *, at_once=False):
        """Sign with ``kid`` (default: the staged key) from now on. The key it replaces stays
        published as ``previous`` until ``retire``; ``at_once`` (suspected compromise) retires
        it, and any other previous key, straight away."""
        with self._lock:
            state = self._require_state()
            if kid is None:
                staged = [k["kid"] for k in state["keys"] if k["state"] == STAGED]
                if len(staged) != 1:
                    msg = "no staged key to activate (run stage first)"
                    raise ClientKeyError(msg)
                kid = staged[0]
            entry = next((k for k in state["keys"] if k["kid"] == kid), None)
            if entry is None or entry["state"] not in (STAGED, ACTIVE):
                msg = f"{kid} is not a staged key"
                raise ClientKeyError(msg)
            self.private_key(kid)  # never switch to a key that cannot sign
            for other in state["keys"]:
                if other is entry:
                    continue
                if other["state"] == ACTIVE:
                    other["state"] = RETIRED if at_once else PREVIOUS
                elif other["state"] == PREVIOUS and at_once:
                    other["state"] = RETIRED
            entry["state"] = ACTIVE
            self._save(state)
            return kid

    def retire(self, kid=None):
        """Stop publishing ``kid`` (default: every previous key). The active key cannot be
        retired: activate another one first."""
        with self._lock:
            state = self._require_state()
            if kid is None:
                chosen = [k for k in state["keys"] if k["state"] == PREVIOUS]
            else:
                chosen = [k for k in state["keys"] if k["kid"] == kid]
                if not chosen:
                    msg = f"no key {kid} in {self.dir}"
                    raise ClientKeyError(msg)
                if chosen[0]["state"] == ACTIVE:
                    msg = f"{kid} is the active key: activate another one first"
                    raise ClientKeyError(msg)
            for entry in chosen:
                entry["state"] = RETIRED
            if chosen:
                self._save(state)
            return [k["kid"] for k in chosen]

    def delete(self, kid=None):
        """Remove retired keys for good (default: every retired key)."""
        with self._lock:
            state = self._require_state()
            if kid is None:
                chosen = [k for k in state["keys"] if k["state"] == RETIRED]
            else:
                chosen = [k for k in state["keys"] if k["kid"] == kid]
                if not chosen or chosen[0]["state"] != RETIRED:
                    msg = f"{kid} is not a retired key: retire it first"
                    raise ClientKeyError(msg)
            if not chosen:
                return []
            gone = {k["kid"] for k in chosen}
            state["keys"] = [k for k in state["keys"] if k["kid"] not in gone]
            self._save(state)  # first: state.json never names a missing file
            for name in gone:
                self._path(f"{name}.pem").unlink(missing_ok=True)
                self._private.pop(name, None)
            return sorted(gone)


# --- the key tool ---------------------------------------------------------------------


def _status_line(key):
    """``<kid> <state> <created, IST> <age in days>``: four fields, for scripts (awk)."""
    created = _created(key).astimezone(IST).isoformat(timespec="minutes")
    return f"{key['kid']} {key['state']} {created} {key['age_days']}"


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m ihr_platform.client_keys",
        description="The keys our processes sign in to Keycloak with (P1-T8d2).",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default=DEFAULT_DIR, help="the key folder")
    common.add_argument(
        "--purpose", choices=PURPOSES, help="required by ensure; checked otherwise"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("ensure", "make an active key if the folder has none"),
        ("stage", "make a new key, published but not used yet"),
        ("public", "print the public key set"),
    ):
        sub = commands.add_parser(name, parents=[common], help=text)
        if name == "stage":
            sub.add_argument(
                "--fresh",
                action="store_true",
                help="suspected compromise: discard any staged key, make a new one",
            )
    for name, text in (
        ("activate", "sign with the staged key, or --kid"),
        ("retire", "stop publishing the previous keys, or --kid"),
        ("delete", "remove the retired keys for good, or --kid"),
    ):
        sub = commands.add_parser(name, parents=[common], help=text)
        sub.add_argument("--kid")
        if name == "activate":
            sub.add_argument(
                "--at-once",
                action="store_true",
                help="suspected compromise: stop publishing the old key now",
            )
    status = commands.add_parser("status", parents=[common], help="every key, its age")
    status.add_argument("--json", action="store_true")
    return parser


def _say(text):
    """One line for the person at the terminal (stderr: stdout is for scripts)."""
    sys.stderr.write(f"{text}\n")


def _run(args, store):
    """The command's stdout lines."""
    if args.command == "ensure":
        if not args.purpose:
            msg = "ensure needs --purpose"
            raise ClientKeyError(msg)
        kid, made = store.ensure(args.purpose)
        _say(f"client key {args.purpose}: {'made' if made else 'kept'} {kid}")
        return [kid]
    held = store.purpose()
    if held is None:
        store.kids(ACTIVE)  # raises the usual "no client key" message
    if args.purpose and held != args.purpose:
        msg = f"{args.dir} holds the {held} key, not {args.purpose}"
        raise ClientKeyError(msg)
    if args.command == "status":
        status = store.status()
        if args.json:
            return [json.dumps(status, indent=1)]
        return [_status_line(k) for k in status["keys"]]
    if args.command == "public":
        return [store.public_jwks()]
    if args.command == "stage":
        done = [store.stage(fresh=args.fresh)]
    elif args.command == "activate":
        done = [store.activate(args.kid, at_once=args.at_once)]
    elif args.command == "retire":
        done = store.retire(args.kid)
    else:
        done = store.delete(args.kid)
    verb = {"stage": "staged", "activate": "activated"}.get(args.command)
    _say(
        f"client key {held}: {verb or args.command + 'd'} {', '.join(done) or 'nothing'}"
    )
    return done


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        lines = _run(args, KeyStore(args.dir))
    except ClientKeyError as exc:
        _say(f"client keys: {exc}")
        return 2
    sys.stdout.write("".join(f"{line}\n" for line in lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
