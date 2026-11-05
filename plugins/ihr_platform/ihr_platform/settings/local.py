"""CARE's local settings in tenant mode (scripts/care-dev.sh)."""

from config.settings.local import *  # noqa: F403

from ihr_platform.settings.tenancy import apply_tenancy

apply_tenancy(globals())
