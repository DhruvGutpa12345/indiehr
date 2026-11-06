from django import template

from ihr_tenant_settings.branding import hospital_name as _hospital_name

register = template.Library()


@register.simple_tag
def hospital_name():
    return _hospital_name()
