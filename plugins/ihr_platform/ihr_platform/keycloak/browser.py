"""A scripted browser for Keycloak's login pages (tests and dev checks only).

It runs the real authorization-code + PKCE flow: it opens the authorize URL, fills the
username/password, OTP and "set up authenticator" forms it recognises, stops at the redirect
back to the app and exchanges the code. Any other page ends the run, so an unexpected page
(e.g. "update your profile") fails a test loudly instead of being skipped.
"""

import base64
import hashlib
import html
import json
import secrets
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http import HTTPStatus
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from . import totp
from .kc_http import request

_last_step = {}  # secret -> last 30 s step used: Keycloak refuses a code twice


@dataclass
class Form:
    id: str
    action: str
    inputs: dict = field(default_factory=dict)


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.links.append(html.unescape(a["href"]))
        if tag == "form":
            self.forms.append(
                Form(a.get("id") or "", html.unescape(a.get("action") or ""))
            )
        elif tag == "input" and self.forms and a.get("name"):
            self.forms[-1].inputs[a["name"]] = a.get("value") or ""


@dataclass
class LoginResult:
    code: str = None
    error: str = None  # OAuth error from the redirect, e.g. "login_required"
    page: str = None  # text of the page where the run stopped, if not redirected
    pages: list = field(default_factory=list)  # ids of the forms that were filled
    tokens: dict = None
    # set when the "set up authenticator" page was completed
    enrolled_secret: str = None
    landed: str = None  # app_sign_in: the app page the browser was sent back to
    nonce: str = None  # login: the nonce sent, to check the ID token against

    @property
    def access(self):
        return decode_jwt(self.tokens["access_token"]) if self.tokens else None


def decode_jwt(token):
    """Claims of a JWT, unverified (tests read claims; P1-T4 verifies signatures)."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class Browser:
    def __init__(self, base_url, realm):
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self.cookies = {}

    @property
    def oidc(self):
        return f"{self.base_url}/realms/{self.realm}/protocol/openid-connect"

    def fetch(self, method, url, form=None):
        headers = {}
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        body = None
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(form)
        resp = request(method, url, headers=headers, body=body)
        for raw in resp.headers.get_all("Set-Cookie") or []:
            name, _, rest = raw.partition("=")
            value = rest.split(";", 1)[0]
            attrs = rest.lower()
            if (
                "max-age=0" in attrs
                or "expires=thu, 01 jan 1970" in attrs
                or value == ""
            ):
                self.cookies.pop(name.strip(), None)
            else:
                self.cookies[name.strip()] = value
        return resp

    def login(
        self,
        client_id,
        redirect_uri,
        username=None,
        password=None,
        otp_secret=None,
        scope="openid",
        extra=None,
        exchange=True,
    ):
        """Run one login. ``otp_secret`` is the raw TOTP secret (or None for no OTP)."""
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scope,
            "state": secrets.token_urlsafe(12),
            "nonce": secrets.token_urlsafe(12),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **(extra or {}),
        }
        result = LoginResult(nonce=params["nonce"])
        url = f"{self.oidc}/auth?{urlencode(params)}"
        location = self._walk(
            url,
            result,
            username,
            password,
            otp_secret,
            lambda u: u.startswith(redirect_uri),
        )
        if location is not None:
            query = parse_qs(urlsplit(location).query)
            if query.get("state", [None])[0] != params["state"]:
                result.error = "state_mismatch"
                return result
            result.code = query.get("code", [None])[0]
            result.error = query.get("error", [None])[0]
        if result.code and exchange:
            token = request(
                "POST",
                f"{self.oidc}/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=urlencode(
                    {
                        "grant_type": "authorization_code",
                        "code": result.code,
                        "redirect_uri": redirect_uri,
                        "client_id": client_id,
                        "code_verifier": verifier,
                    }
                ),
            )
            if token.status != HTTPStatus.OK:
                result.error = f"token:{token.status}:{token.text[:200]}"
            else:
                result.tokens = token.json()
        return result

    def setup_account(self, link, new_password):
        """Follow an e-mailed setup link (P1-T5c) through Keycloak's pages: "proceed", set up
        the authenticator, choose a password. Stops on the first page it does not know (the
        "account updated" page when all went well), kept in ``result.page``."""
        result = LoginResult()
        self._walk(
            link, result, None, None, None, lambda u: False, new_password=new_password
        )
        return result

    def _walk(
        self, url, result, username, password, otp_secret, done, new_password=None
    ):
        """Follow redirects and fill the forms we know until ``done(location)``; returns that
        location, or None when the run stops on a page (kept in ``result.page``)."""
        resp = self.fetch("GET", url)
        for _ in range(16):
            if resp.status in (301, 302, 303):
                location = urljoin(url, resp.headers["Location"])
                if done(location):
                    return location
                url = location
                resp = self.fetch("GET", url)
                continue
            text = resp.text
            parser = _FormParser()
            parser.feed(text)
            forms = {f.id: f for f in parser.forms}
            if (
                "kc-form-login" in forms
                and username is not None
                and "username" not in result.pages
            ):
                form = forms["kc-form-login"]
                data = dict(form.inputs, username=username, password=password or "")
                result.pages.append("username")
            elif (
                "kc-otp-login-form" in forms
                and otp_secret
                and "otp" not in result.pages
            ):
                form = forms["kc-otp-login-form"]
                data = dict(form.inputs, otp=_fresh_code(otp_secret))
                result.pages.append("otp")
            elif (
                "kc-passwd-update-form" in forms
                and new_password
                and "new-password" not in result.pages
            ):
                form = forms["kc-passwd-update-form"]
                data = dict(
                    form.inputs,
                    **{"password-new": new_password, "password-confirm": new_password},
                )
                result.pages.append("new-password")
            elif (
                new_password
                and "proceed" not in result.pages
                and any("/login-actions/action-token" in link for link in parser.links)
            ):
                # "Perform the following actions: ... Click here to proceed"
                link = next(
                    x for x in parser.links if "/login-actions/action-token" in x
                )
                result.pages.append("proceed")
                url = urljoin(url, link)
                resp = self.fetch("GET", url)
                continue
            elif (
                "kc-totp-settings-form" in forms and "configure-otp" not in result.pages
            ):
                form = forms["kc-totp-settings-form"]
                secret = form.inputs["totpSecret"]
                data = dict(form.inputs, totp=_fresh_code(secret), userLabel="test")
                result.pages.append("configure-otp")
                result.enrolled_secret = secret
            else:
                result.page = text
                result.pages.append(f"stopped:{resp.status}")
                return None
            url = urljoin(url, form.action)
            resp = self.fetch("POST", url, data)
        result.pages.append("too-many-steps")
        return None

    def app_sign_in(
        self, app, username=None, password=None, otp_secret=None, next_path="/"
    ):
        """Sign in to a hospital the way its web app does: start at ``<app>/api/ihr/auth/login/``
        and stop when the app sends the browser back to one of its own pages. Returns the
        result with ``landed`` set to that page (or to ``/login?error=...``)."""
        result = LoginResult()
        host = urlsplit(app).netloc

        def back_in_app(location):
            parts = urlsplit(location)
            return parts.netloc in ("", host) and not parts.path.startswith(
                "/api/ihr/auth/"
            )

        result.landed = self._walk(
            f"{app}/api/ihr/auth/login/?{urlencode({'next': next_path})}",
            result,
            username,
            password,
            otp_secret,
            back_in_app,
        )
        return result

    def refresh(self, client_id, refresh_token):
        return request(
            "POST",
            f"{self.oidc}/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                }
            ),
        )


def _fresh_code(secret):
    """A code from a 30 s window not used before for this secret (codes are single-use)."""
    while True:
        step = int(time.time() // totp.PERIOD)
        if _last_step.get(secret, -1) < step:
            _last_step[secret] = step
            return totp.code(secret)
        time.sleep(totp.PERIOD - time.time() % totp.PERIOD + 0.2)
