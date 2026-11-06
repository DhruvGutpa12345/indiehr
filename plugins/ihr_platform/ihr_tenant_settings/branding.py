"""The current hospital's name and default logo, for emails and the web app (P1-T2b)."""

from django.utils.html import escape

from ihr_tenant_settings.models import HospitalProfile


def hospital_name():
    """The hospital being served (``settings.SITE_NAME_FUNCTION`` in tenant mode)."""
    return HospitalProfile.current().display_name


def initials(name):
    """Up to two letters from the first two words: "Alpha Hospital" -> "AH"."""
    words = ("".join(c for c in word if c.isalnum()) for word in name.split())
    return "".join(word[0] for word in words if word)[:2].upper() or "H"


def default_logo_svg(profile):
    """A plain mark (initials on the brand colour) until the hospital uploads its logo."""
    label = escape(initials(profile.short_name or profile.display_name))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" role="img">'
        f"<title>{escape(profile.display_name)}</title>"
        f'<rect width="96" height="96" rx="20" fill="{profile.primary_colour}"/>'
        '<text x="48" y="50" fill="#fff" font-family="system-ui,sans-serif" '
        'font-size="38" font-weight="700" text-anchor="middle" '
        f'dominant-baseline="central">{label}</text></svg>'
    )
