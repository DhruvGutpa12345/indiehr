"""The identifier allow-list on search and writes (P1-T11b1, review S2; "After the attack" §2.4).

The serializer guard (guard.py) keeps a programme identifier (an ART or Nikshay registration,
whose very presence reveals the illness) out of every body a restricted user gets. CARE's
``patient/search`` is open to every signed-in user and searches by any identifier
configuration, so without this a restricted user could search a programme number and learn from
the answer whether that person is registered. ``install()`` wraps two actions of CARE's
``PatientViewSet``; for a restricted user (``state.restricted``) naming a configuration whose
system is not on ``fields/identifier_configs.txt``:

- ``search``: 403, nothing searched;
- ``update_identifier``: 400, before the patient is read or anything written (b1's restricted
  roles cannot reach this action on a patient anyway: CARE answers 404; P1-T11b2 widens it).

A configuration that does not exist, a malformed one and a request without one
(``phone_number``) are left to CARE, which answers them as before. ``search_retrieve`` takes no
configuration (a phone number, a year of birth and the start of the patient's id); its answer is
a patient body, which the guard projects. Checked by ihr_access.E022: the actions on CARE's
class, and on any subclass (P1-T11b2's swap), are these wrappers.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import PermissionDenied, ValidationError

from ihr_access import state

MARKER = "ihr_guard"
REFUSED_SEARCH = "Your role cannot search by this identifier."
REFUSED_WRITE = "Your role cannot change this identifier."


def _refuse_search():
    raise PermissionDenied(REFUSED_SEARCH)


def _refuse_write():
    raise ValidationError(REFUSED_WRITE)


ACTIONS = {"search": _refuse_search, "update_identifier": _refuse_write}


def off_list(config_id):
    """The identifier configuration ``config_id`` exists and its system is not on
    ``fields/identifier_configs.txt``. A malformed or unknown id is not (CARE answers it).
    Shared by the restricted users' search and writes here, the demographics edit and the
    patient create of P1-T11b2 (views.IhrPatientViewSet, edit.allowed_identifiers)."""
    from care.emr.models.patient import PatientIdentifierConfig

    from ihr_access.guard import allowed_identifier_systems

    if not config_id:
        return False
    try:
        config = PatientIdentifierConfig.objects.filter(external_id=config_id).first()
    except (DjangoValidationError, ValueError, TypeError):
        return False  # not an id: CARE refuses the request itself
    if config is None:
        return False  # CARE answers "not found"
    inner = config.config if isinstance(config.config, dict) else {}
    return inner.get("system") not in allowed_identifier_systems()


def requested_config(data):
    return data.get("config") if hasattr(data, "get") else None


def hidden_config(user, data):
    """A restricted user names an identifier configuration whose system they may not use."""
    if user is None or getattr(user, "is_superuser", False):
        return False
    config_id = requested_config(data)
    if not config_id or not state.for_user(user).restricted:
        return False
    return off_list(config_id)


def _wrapper(original, refuse):
    def action(self, request, *args, **kwargs):
        if hidden_config(request.user, request.data):
            refuse()
        return original(self, request, *args, **kwargs)

    # DRF's action attributes (mapping, detail, url_path, the schema) come along
    action.__dict__.update(original.__dict__)
    for name in ("__name__", "__qualname__", "__doc__", "__module__"):
        setattr(action, name, getattr(original, name))
    action.ihr_original = original
    setattr(action, MARKER, True)
    return action


def install():
    from care.emr.api.viewsets.patient import PatientViewSet

    for name, refuse in ACTIONS.items():
        original = getattr(PatientViewSet, name)
        if getattr(original, MARKER, False):
            continue
        setattr(PatientViewSet, name, _wrapper(original, refuse))


def _classes(cls):
    yield cls
    for sub in cls.__subclasses__():
        yield from _classes(sub)


def problems():
    """Why the allow-list does not hold on search and writes (E022)."""
    from care.emr.api.viewsets.patient import PatientViewSet

    return [
        f"{cls.__module__}.{cls.__qualname__}.{name} is not guarded"
        for cls in _classes(PatientViewSet)
        for name in ACTIONS
        if not getattr(getattr(cls, name, None), MARKER, False)
    ]
