"""The slug sets the web app needs, written for it as JSON (P1-T11c1; design-c.md §2.1, §13.2 FF2).

The backend is the source: ``registry.py`` and ``permissions.py``. ``frontend_slugs.json`` next
to this file is what ``manage.py ihr_frontend_slugs --write`` writes from them;
``ihr_roles.tests.test_frontend_slugs`` fails when the file differs, and
``scripts/check-ihr-frontend.py`` (host side) byte-compares it with the web app's copy
``apps/frontend/src/Utils/ihr/ihrSlugs.json``.

The sets only decide which items the web app shows inside a mode; the mode itself (restricted
or not) is the backend's decision, published by ``GET /api/ihr/my-access/`` (Q-K49 A').

Keys (sorted, every list sorted, two-space indent, newline at the end):
    bedForBilling   role names that see the visit's bed (registry.BED_FOR_BILLING, Q-K37a B)
    clinical        registry.CLINICAL_SLUGS
    grants          {role name: [slug, ...]} (registry.GRANTS, the hidden role included)
    ours            our permission slugs (permissions.SLUGS)
    patientOpening  registry.PATIENT_OPENING
    restricted      role names of registry.RESTRICTED
"""

import json
from pathlib import Path

PATH = Path(__file__).with_name("frontend_slugs.json")


def slug_sets():
    from ihr_roles import permissions, registry

    return {
        "bedForBilling": sorted(registry.BED_FOR_BILLING),
        "clinical": sorted(registry.CLINICAL_SLUGS),
        "grants": {
            name: sorted(set(slugs)) for name, slugs in sorted(registry.GRANTS.items())
        },
        "ours": sorted(permissions.SLUGS),
        "patientOpening": sorted(registry.PATIENT_OPENING),
        "restricted": sorted(registry.RESTRICTED),
    }


def render():
    """The file's exact text."""
    return json.dumps(slug_sets(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
