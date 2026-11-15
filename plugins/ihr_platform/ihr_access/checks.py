"""System checks of ihr_access.

- ihr_access.E022: the guard and the PatientUser rule sit on the classes CARE actually uses (an
  upstream sync that moves a spec, or a subclass that defines its own ``serialize``, would
  otherwise bypass them silently); the visit check (visits.py) is installed and no view under a
  patient overrides ``initial`` outside our code.
- ihr_access.E023: the records grant authorizer (authz.RecordsGrantAuthorizer) answers each of
  CARE's read checks it wraps, delegates to a CARE handler that still defines it, answers no
  write and never ``can_view_clinical_data``; the signed-link cap is installed (P1-T11b4).
- ihr_access.E024: the encounter filter allow-list classifies every filter of CARE's encounter
  list (P1-T11b2, "After the attack" §6.2), and fields/encounter_create.txt every field of
  CARE's encounter create spec (review C2); a filter or field upstream adds would otherwise be
  refused to restricted users without review (fail closed, but unexplained).
"""

from django.core.checks import Error


def guard_installed(app_configs=None, **kwargs):
    from ihr_access import authz, exceptions, guard, identifiers, visits

    return [
        Error(f"the access guard does not hold: {problem}", id="ihr_access.E022")
        for problem in (
            guard.problems()
            + authz.problems()
            + exceptions.problems()
            + identifiers.problems()
            + visits.problems()
        )
    ]


def filters_classified(app_configs=None, **kwargs):
    from ihr_access import views

    return [
        Error(f"an encounter allow-list is stale: {problem}", id="ihr_access.E024")
        for problem in views.filter_problems() + views.create_problems()
    ]


def records_grants(app_configs=None, **kwargs):
    from ihr_access import authz, records

    return [
        Error(f"records grants do not hold: {problem}", id="ihr_access.E023")
        for problem in authz.grant_problems() + records.problems()
    ]
