"""System check ihr_access.E022: the guard and the PatientUser rule sit on the classes CARE
actually uses (an upstream sync that moves a spec, or a subclass that defines its own
``serialize``, would otherwise bypass them silently)."""

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
