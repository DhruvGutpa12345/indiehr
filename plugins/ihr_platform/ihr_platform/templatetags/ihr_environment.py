"""``{% synthetic_banner %}``: the operator console's "synthetic data only" bar (P1-T32 E5)."""

from django import template
from django.utils.html import format_html

from ihr_platform import environment

register = template.Library()


@register.simple_tag
def synthetic_banner():
    """Both languages, each with the class (the console's CSP is style-src 'self': no inline
    style, so an unclassed Hindi line would render unstyled). Nothing on production."""
    if not environment.synthetic():
        return ""
    return format_html(
        '<div class="ihr-synthetic" lang="en">{}</div>'
        '<div class="ihr-synthetic" lang="hi">{}</div>',
        environment.BANNER_EN,
        environment.BANNER_HI,
    )
