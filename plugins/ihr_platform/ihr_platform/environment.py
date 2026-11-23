"""Which instance is this? The one place that knows the environment names (P1-T32 E1, ADR-020).

``IHR_ENVIRONMENT`` is written once by ``scripts/staging.sh init`` into an instance's ``.env``
and passed by Compose to every process that needs it. Nothing defaults it on a server any more
(``settings/secure.py`` refuses a missing or unknown value).

| value             | meaning                                                              |
|-------------------|----------------------------------------------------------------------|
| ``production-<n>``| a production cell (n from 1 to 999, no leading zero)                |
| ``staging``       | a synthetic server (ADR-020 §2 A)                                     |
| ``proof``         | a local proof, the throwaway VM, a throwaway copy                    |
| *(unset)*         | dev (``care-dev.sh``), tests and CI: reported as ``dev``             |

"Synthetic" means "not production". Every check asks "is this production?", and a name that
is not exactly ``dev``, ``staging`` or ``proof`` counts as production, so a typo can never
relax a production rule.

``SERVER_PATTERN`` is the canonical regex. Its copies (shell, scripts) are checked against it by
``scripts/test_environment_regex.py`` and ``ihr_platform/tests/test_environment.py``.

This module must not import ``django.conf`` at import time: the settings modules import it.
"""

import re

SERVER_PATTERN = r"^(production-[1-9][0-9]{0,2}|staging|proof)$"
SERVER_RE = re.compile(SERVER_PATTERN)

DEV = "dev"
SYNTHETIC_NAMES = (DEV, "staging", "proof")
# The proof stands' domain suffix (staging.sh LOCAL_SUFFIX, deploy.sh PROOF_SUFFIX is
# "app." + this): a production instance never runs on it (secure.py).
PROOF_DOMAIN = "ihr-proof.in"

# E5: the bar every web-app page and the operator console show on a synthetic instance
# (ADR-020 §3). In Python, not deploy/locale/: bootstrap.js runs before the web app's i18n
# loads and the console is Django. The Hindi is in docs/i18n/review/hi-batch-8.csv for the owner.
BANNER_EN = "Synthetic data only: do not enter real patients."
BANNER_HI = "केवल कृत्रिम (सिंथेटिक) डेटा: असली मरीज़ों की जानकारी दर्ज न करें।"


def valid(value):
    """True only for exactly ``production-<n>``, ``staging`` or ``proof`` (no spaces, quotes, CR)."""
    return isinstance(value, str) and SERVER_RE.fullmatch(value) is not None


def name():
    """This process's environment: the setting, or ``dev`` when it is unset."""
    from django.conf import settings  # never at import time

    return str(getattr(settings, "IHR_ENVIRONMENT", "") or "") or DEV


def is_production(value=None):
    """True unless ``value`` (default: this process's name) is exactly dev, staging or proof."""
    if value is None:
        value = name()
    return value not in SYNTHETIC_NAMES


def synthetic(value=None):
    """Not production: a throwaway or synthetic instance that must never hold real data."""
    return not is_production(value)
