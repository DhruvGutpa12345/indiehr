"""Settings that must hold wherever hospitals are served (ADR-009)."""

from django.conf import settings
from django.core.checks import Error, register
from django.core.checks import Warning as CheckWarning

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


@register()
def staff_directory(app_configs, **kwargs):
    """ihr_identity.E027: the staff directory's trigger (P1-T31a, directory.py) is installed on
    post_migrate, and CARE's user model still has the columns it names. Static only: drift in
    a hospital schema is reported by ``ihr_staff_directory --check`` and never refuses a
    process or gates a hospital (docs/reviews/p1-t31/attack.md D1).
    ihr_identity.W027: its SQL differs from the one the newest ihr_identity migration that
    states one gives, so existing hospitals would not get it (a warning, so a mutation run still
    starts; Upkeep.test_w027_wants_a_migration_stating_this_builds_sql fails on it)."""
    from django.apps import apps
    from django.db.models.signals import post_migrate

    from ihr_identity import directory

    uid = apps.get_app_config("ihr_identity").DIRECTORY_UID
    installed = any(receiver[0][0] == uid for receiver in post_migrate.receivers)
    found = [
        Error(problem, id="ihr_identity.E027")
        for problem in directory.static_problems(installed=installed)
    ]
    found += [
        CheckWarning(problem, id="ihr_identity.W027")
        for problem in directory.code_problems()
    ]
    return found
