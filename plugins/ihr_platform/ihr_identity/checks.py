"""Settings that must hold wherever hospitals are served (ADR-009)."""

from django.conf import settings
from django.core.checks import Error, register

STAFF_CLASSES = [
    "ihr_identity.authentication.StaffSessionAuthentication",
    "ihr_identity.authentication.ServiceTokenAuthentication",
]


@register()
def staff_sign_in_settings(app_configs, **kwargs):
    errors = []
    classes = settings.REST_FRAMEWORK.get("DEFAULT_AUTHENTICATION_CLASSES")
    if list(classes or []) != STAFF_CLASSES:
        errors.append(
            Error(
                f"Authentication classes must be exactly {STAFF_CLASSES}.",
                id="ihr_identity.E001",
            )
        )
    if getattr(settings, "CORS_ALLOW_CREDENTIALS", False):
        errors.append(
            Error("CORS_ALLOW_CREDENTIALS must stay off.", id="ihr_identity.E002")
        )
    if not settings.DEBUG:
        if (
            not settings.IHR_SESSION_COOKIE.startswith("__Host-")
            or not settings.IHR_SESSION_COOKIE_SECURE
        ):
            errors.append(
                Error(
                    "Outside development the session cookie must be __Host- and Secure.",
                    id="ihr_identity.E003",
                )
            )
        if not getattr(settings, "IHR_BACKCHANNEL_LOGOUT_URL", ""):
            errors.append(
                Error(
                    "Set IHR_BACKCHANNEL_LOGOUT_URL (the platform host's "
                    "/api/ihr/backchannel-logout/ as Keycloak reaches it), so a sign-out "
                    "ends every hospital's session at once.",
                    id="ihr_identity.E006",
                )
            )
        if not str(getattr(settings, "IHR_KEYCLOAK_PUBLIC_URL", "")).startswith(
            "https://"
        ):
            errors.append(
                Error(
                    "IHR_KEYCLOAK_PUBLIC_URL must be https:// outside development.",
                    id="ihr_identity.E005",
                )
            )
    for name in ("IHR_KEYCLOAK_PUBLIC_URL", "IHR_KEYCLOAK_URL", "IHR_KEYCLOAK_REALM"):
        if not getattr(settings, name, None):
            errors.append(Error(f"Set {name}.", id="ihr_identity.E004"))
    if not settings.DEBUG:
        # The same check ihr_platform.settings.deployment makes at start (P1-T8d2), here so
        # `manage.py check` names it too.
        from ihr_platform.settings.secure import client_key_problems

        errors += [
            Error(problem, id="ihr_identity.E007")
            for problem in client_key_problems(
                getattr(settings, "IHR_CLIENT_KEY_PURPOSE", ""),
                settings.IHR_CLIENT_KEY_DIR,
            )
        ]
    return errors
