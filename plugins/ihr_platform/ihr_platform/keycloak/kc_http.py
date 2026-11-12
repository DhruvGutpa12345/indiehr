"""Small HTTP and Keycloak admin client (standard library only).

Never logs request bodies or Authorization headers: they carry secrets.
"""

import json
import time
from dataclasses import dataclass
from email.message import Message
from http import HTTPStatus
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import quote, urlencode, urlsplit


@dataclass
class Response:
    status: int
    headers: Message
    body: bytes

    def json(self):
        return json.loads(self.body) if self.body else None

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")


def request(method, url, headers=None, body=None, timeout=30):
    """One HTTP request, no redirects followed.

    ``*.localhost`` names connect to 127.0.0.1 (keeping the Host header): not every resolver
    maps them, and some return ::1 first while dev services listen on 127.0.0.1 only.
    """
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme == "https":
        conn = HTTPSConnection(host, parts.port or 443, timeout=timeout)
    else:
        if host == "localhost" or host.endswith(".localhost"):
            host = "127.0.0.1"
        conn = HTTPConnection(host, parts.port or 80, timeout=timeout)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    try:
        conn.request(
            method, path, body=body, headers={"Host": parts.netloc, **(headers or {})}
        )
        resp = conn.getresponse()
        return Response(resp.status, resp.headers, resp.read())
    finally:
        conn.close()


class KeycloakError(RuntimeError):
    pass


class KeycloakAdmin:
    """Admin REST API client using a client-credentials service account.

    ``account_realm`` is the realm that holds the service account (``master`` for the dev/CI
    bootstrap client); ``realm`` is the realm being managed.

    The account proves who it is either with ``client_secret`` (the operator's bootstrap client
    only) or with ``client_auth``, a callable that returns fresh form fields for each token
    request (the workers' signed assertions, P1-T8d2). It is injected so this module stays
    standard library only: the signing lives in ``ihr_platform.client_keys``.
    """

    def __init__(
        self,
        base_url,
        client_id,
        client_secret,
        realm,
        account_realm="master",
        *,
        client_auth=None,
    ):
        if (client_secret is None) == (client_auth is None):
            msg = "Give the account either a client secret or client_auth, not both"
            raise ValueError(msg)
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self._client_auth = client_auth or (lambda: {"client_secret": client_secret})
        self._account_realm = account_realm
        self._token = None
        self._token_expiry = 0.0

    def _access_token(self):
        if self._token and time.monotonic() < self._token_expiry:
            return self._token
        resp = request(
            "POST",
            f"{self.base_url}/realms/{self._account_realm}/protocol/openid-connect/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    **self._client_auth(),
                }
            ),
        )
        if resp.status != HTTPStatus.OK:
            # Keycloak's error code only (e.g. invalid_client); our request is never shown.
            try:
                error = (resp.json() or {}).get("error", "")
            except ValueError:
                error = ""
            msg = f"Keycloak admin login failed: HTTP {resp.status} {error}".rstrip()
            raise KeycloakError(msg)
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.monotonic() + max(data.get("expires_in", 60) - 20, 5)
        return self._token

    def call(self, method, path, body=None, form=None, ok=(200, 201, 204)):
        """``path`` is relative to /admin/realms/<realm> unless it starts with '/'."""
        url = (
            f"{self.base_url}/admin/realms{path.rstrip('/')}"
            if path.startswith("/")
            else f"{self.base_url}/admin/realms/{self.realm}/{path}"
        )
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        data = None
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = urlencode(form)
        elif body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body)
        resp = request(method, url, headers=headers, body=data)
        if resp.status not in ok:
            # The response body is Keycloak's error text, never our request. The path loses its
            # query: lookups carry e-mail addresses and user names (users?email=...), and this
            # message reaches the logs through tracebacks (P1-T9b).
            shown = path.split("?", 1)[0]
            msg = f"{method} {shown}: HTTP {resp.status} {resp.text[:300]}"
            raise KeycloakError(msg)
        return resp

    def get(self, path, ok=(200,)):
        return self.call("GET", path, ok=ok).json()

    def post(self, path, body=None, ok=(201, 204), **kw):
        return self.call("POST", path, body=body, ok=ok, **kw)

    def put(self, path, body=None, ok=(204,)):
        return self.call("PUT", path, body=body, ok=ok)

    def delete(self, path, ok=(204,)):
        return self.call("DELETE", path, ok=ok)

    def created_id(self, resp):
        return resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1]


def q(value):
    """Quote one path segment (flow aliases contain spaces)."""
    return quote(value, safe="")
