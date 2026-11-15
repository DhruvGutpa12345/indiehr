"""System checks of ihr_access.

- ihr_access.E022: the guard and the PatientUser rule sit on the classes CARE actually uses (an
  upstream sync that moves a spec, or a subclass that defines its own ``serialize``, would
  otherwise bypass them silently).
- ihr_access.E024: the encounter filter allow-list classifies every filter of CARE's encounter
  list (P1-T11b2, "After the attack" §6.2), and fields/encounter_create.txt every field of
  CARE's encounter create spec (review C2); a filter or field upstream adds would otherwise be
  refused to restricted users without review (fail closed, but unexplained).
"""

from django.core.checks import Error


def guard_installed(app_configs=None, **kwargs):
    from ihr_access import authz, exceptions, guard, identifiers

    return [
        Error(f"the access guard does not hold: {problem}", id="ihr_access.E022")
        for problem in (
            guard.problems()
            + authz.problems()
            + exceptions.problems()
            + identifiers.problems()
        )
    ]


def filters_classified(app_configs=None, **kwargs):
    from ihr_access import views

    return [
        Error(f"an encounter allow-list is stale: {problem}", id="ihr_access.E024")
        for problem in views.filter_problems() + views.create_problems()
    ]
