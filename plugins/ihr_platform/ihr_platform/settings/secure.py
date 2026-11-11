"""Refuse to start a staging or production server with unsafe settings (P1-T8a, review R0-09).

CARE's own settings fall back to a published secret key, every host allowed, and a signing
key read from a file in the image. ``problems()`` lists everything that would make a
deployment unsafe; ``ihr_platform.settings.deployment`` raises if the list is not empty, so
the process never serves a request with them. Pure: takes the settings and the environment,
so tests can feed it anything.
"""

import re

# CARE's base.py default (config/settings/base.py): published in its repository.
CARE_DEFAULT_SECRET_KEY = (
    "eXZQzOzx8gV38rDG0Z0fFZWweUGl3LwMZ9aTKqJiXQTI0nKMh0Z7sbHfqT8KFEnd"
)
MIN_SECRET_KEY_LENGTH = 50
MIN_SECRET_KEY_DISTINCT = 20  # characters; rejects 'aaaa…' and other patterns
MIN_HSTS_SECONDS = 86400
# Placeholders as people write them; random keys may contain these letters anywhere else.
WEAK_SECRET_RE = re.compile(
    r"^(django-)?insecure|change[-_ ]?me|^(test|example|default|secret|dev)[-_]",
    re.IGNORECASE,
)
LOCAL_SUFFIXES = ("localhost", "local", "test", "example", "invalid")
LOCAL_MAIL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
# Environment variables a deployment must set itself (no default may stand in for them).
REQUIRED_ENV = (
    "DJANGO_SECRET_KEY",
    "DJANGO_ALLOWED_HOSTS",
    "DATABASE_URL",
    "REDIS_URL",
    "JWKS_BASE64",
    "IHR_TENANT_DOMAIN_SUFFIX",
    "IHR_KEYCLOAK_PUBLIC_URL",
    "IHR_KEYCLOAK_URL",
    "IHR_CONSOLE_URL",
    "IHR_BACKCHANNEL_LOGOUT_URL",
    "FILE_UPLOAD_BUCKET",
    "FACILITY_S3_BUCKET",
)


def _https(value):
    return str(value or "").startswith("https://")


def problems(settings, environ):  # noqa: PLR0912 (one flat list of rules)
    """Everything unsafe about these settings, as sentences; empty when safe."""
    found = [
        f"{name} is not set."
        for name in REQUIRED_ENV
        if not environ.get(name, "").strip()
    ]

    if settings.get("DEBUG"):
        found.append("DEBUG must be off.")
    if not settings.get("IS_PRODUCTION"):
        # Off, CARE gives every patient the sign-in code 45612 and shows its API schema.
        found.append("IS_PRODUCTION must be on.")

    key = str(settings.get("SECRET_KEY", ""))
    if key == CARE_DEFAULT_SECRET_KEY:
        found.append("SECRET_KEY is CARE's published default.")
    elif len(key) < MIN_SECRET_KEY_LENGTH or len(set(key)) < MIN_SECRET_KEY_DISTINCT:
        found.append(
            f"SECRET_KEY must be random, at least {MIN_SECRET_KEY_LENGTH} characters."
        )
    elif WEAK_SECRET_RE.search(key):
        found.append("SECRET_KEY looks like a placeholder.")

    suffix = str(settings.get("IHR_TENANT_DOMAIN_SUFFIX", "")).strip(".").lower()
    if not suffix or suffix.rsplit(".", 1)[-1] in LOCAL_SUFFIXES:
        suffix = ""  # reported once, here; the host rule below cannot apply
        found.append(
            "IHR_TENANT_DOMAIN_SUFFIX must be a real domain (e.g. app.example.in)."
        )

    hosts = list(settings.get("ALLOWED_HOSTS") or [])
    if not hosts or any(h.strip() in {"*", ".", ""} for h in hosts):
        found.append("ALLOWED_HOSTS must list the hosts; '*' is not allowed.")
    elif suffix and f".{suffix}" not in hosts:
        found.append(
            f"ALLOWED_HOSTS must include .{suffix} (every hospital's address)."
        )

    for name in ("IHR_KEYCLOAK_PUBLIC_URL", "IHR_CONSOLE_URL"):
        if not _https(settings.get(name)):
            found.append(f"{name} must be https://.")
    if not str(settings.get("IHR_FRONTEND_URL_TEMPLATE", "")).startswith("https://"):
        found.append("IHR_FRONTEND_URL_TEMPLATE must be https://.")

    for name in (
        "SESSION_COOKIE_SECURE",
        "CSRF_COOKIE_SECURE",
        "IHR_SESSION_COOKIE_SECURE",
    ):
        if not settings.get(name):
            found.append(f"{name} must be on.")
    for name in ("IHR_SESSION_COOKIE", "IHR_OPS_SESSION_COOKIE"):
        if not str(settings.get(name, "")).startswith("__Host-"):
            found.append(f"{name} must start with __Host-.")
    if int(settings.get("SECURE_HSTS_SECONDS") or 0) < MIN_HSTS_SECONDS:
        found.append(f"SECURE_HSTS_SECONDS must be at least {MIN_HSTS_SECONDS}.")
    if settings.get("SECURE_PROXY_SSL_HEADER") != ("HTTP_X_FORWARDED_PROTO", "https"):
        found.append(
            "SECURE_PROXY_SSL_HEADER must trust X-Forwarded-Proto from the proxy."
        )

    # Every "day" on the server (date filters, beat jobs, slots) is an IST day (P1-T7b).
    if settings.get("TIME_ZONE") != "Asia/Kolkata":
        found.append("TIME_ZONE must be Asia/Kolkata (India Standard Time).")
    if settings.get("USE_TZ") is not True:
        found.append("USE_TZ must be on (times stored with their zone).")

    terminology = str(settings.get("SNOWSTORM_DEPLOYMENT_URL", ""))
    if terminology and not re.match(r"^https://|^http://127\.0\.0\.1:9/", terminology):
        # CARE's default points at a third party over plain HTTP (P1-T10).
        found.append(
            "SNOWSTORM_DEPLOYMENT_URL must be our own https:// service or the black hole."
        )
    if settings.get("SENTRY_DSN"):
        found.append(
            "SENTRY_DSN must stay unset (no data leaves India; P1-T9 monitoring)."
        )
    found += mail_problems(settings)
    return found


def client_key_problems(purpose, key_dir):
    """This process's Keycloak client key (P1-T8d2). Not pure: it reads the key folder.

    A process that declares a purpose (the backend: "backend") must hold exactly that key,
    readable, mode 600, with an active key that loads: otherwise every sign-in would fail, so
    it refuses to start instead. Any process that finds a key must be able to use it."""
    from ihr_platform.client_keys import PURPOSES, ClientKeyError, KeyStore

    if purpose and purpose not in PURPOSES:
        return [f"IHR_CLIENT_KEY_PURPOSE must be one of {', '.join(PURPOSES)}."]
    store = KeyStore(key_dir)
    try:
        held = store.purpose()
        if held is None:
            if purpose:
                return [
                    f"No {purpose} client key in {key_dir}: mount its volume and run the "
                    "key tool (keys ensure)."
                ]
            return []
        if purpose and held != purpose:
            return [
                f"{key_dir} holds the {held} client key; this process signs as {purpose} "
                "and must never hold another process's key."
            ]
        store.check()
    except ClientKeyError as exc:
        return [f"Client key: {exc}"]
    return []


def mail_problems(settings):
    """Invitations (Django) and setup e-mails (Keycloak) must really leave, over TLS, from our
    own sender (P1-T8b4; scripts/staging.sh mail-init)."""
    found = []
    # Any other backend (console, file, locmem) sends nothing; the console one would also print
    # one-time join and setup links into the logs (kept 180 days)
    if settings.get("EMAIL_BACKEND") != SMTP_BACKEND:
        found.append(f"EMAIL_BACKEND must be {SMTP_BACKEND}.")
    if str(settings.get("EMAIL_HOST", "")).strip().lower() in LOCAL_MAIL_HOSTS:
        found.append(
            "EMAIL_HOST is not set: no e-mail would leave (scripts/staging.sh mail-init)."
        )
    if not (settings.get("EMAIL_USE_TLS") or settings.get("EMAIL_USE_SSL")):
        found.append("EMAIL_USE_TLS (or EMAIL_USE_SSL) must be on.")
    sender = str(settings.get("DEFAULT_FROM_EMAIL", "")).lower()
    if "@" not in sender or "@localhost" in sender or "ohc.network" in sender:
        found.append(
            "EMAIL_FROM must be our own sender (e.g. IndieHR <no-reply@amhcloud.in>)."
        )
    smtp = settings.get("IHR_KEYCLOAK_SMTP") or {}
    if str(smtp.get("host", "")).strip().lower() in LOCAL_MAIL_HOSTS or not smtp.get(
        "from"
    ):
        found.append(
            "IHR_KEYCLOAK_SMTP_HOST and _FROM must be set (Keycloak's setup e-mails)."
        )
    elif "true" not in {str(smtp.get(k, "")).lower() for k in ("starttls", "ssl")}:
        found.append("Keycloak's mail server must use TLS (starttls or ssl).")
    return found
