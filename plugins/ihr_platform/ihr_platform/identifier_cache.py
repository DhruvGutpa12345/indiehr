"""CARE's identifier config lookup, made right in every web process (P1-T15b2a, Q-K64;
docs/reviews/p1-t15b/design.md §3.2 and "After the attack" item 16).

``PatientIdentifierConfigCache.get_config`` keeps a per-process dict that nothing ever clears:
after any change to a config (a pack apply, or CARE's own update), every other web process
shows the old display, regex and retrieve options until it restarts. At app start we replace
it with CARE's own Redis-backed ``model_from_cache`` for the same spec, so the answer is the
same, its key carries the hospital's schema (the cache's key function) and CARE's post_save
signal clears it in every process at once. No edit inside ``apps/``.

CARE clears its Redis copy in post_save, inside the writer's transaction: a reader in another
process before the commit would put the old config back for the cache's timeout. So the copy is
cleared once more after the commit (review C4).

``ihr_platform.E025`` refuses to start (any ``manage.py`` command runs the system checks;
``migrate`` in the start-up one-shot) when the swap is not in place, e.g. after an upstream
change to CARE's class (``install`` then leaves it alone, so the check sees it). CARE's own
``get_config`` and its callers are pinned below; ``ihr_platform.tests.test_identifier_cache.
CarePins`` fails when an upstream sync changes them: re-read CARE's change, make ours answer
the same, then pin the new values.
"""

from functools import partial

_SWAPPED = "ihr_swapped"
# sha256 of inspect.getsource(CARE's PatientIdentifierConfigCache.get_config) at the pin
CARE_GET_CONFIG_SHA256 = (
    "c50fd8a970ac5a86ce59dfc918d2b5307afa58544b4613e94e84a4f44abbef5f"
)
# CARE modules that look a config up, and how many calls of
# "PatientIdentifierConfigCache.get_config(" each holds
CARE_CALLERS = {"care.emr.resources.patient.spec": 2}


def get_config(cls, config_id):
    """What CARE's ``get_config`` answers (``PatientIdentifierListSpec`` as JSON), read through
    CARE's Redis-backed cache. A config soft-deleted since is still answered (CARE's cached
    spec reads deleted rows too): old values keep resolving."""
    from care.emr.models.patient import PatientIdentifierConfig
    from care.emr.resources.base import model_from_cache
    from care.emr.resources.patient_identifier.spec import PatientIdentifierListSpec

    data = model_from_cache(PatientIdentifierListSpec, external_id=config_id)
    if data is None:
        msg = "PatientIdentifierConfig matching query does not exist."
        raise PatientIdentifierConfig.DoesNotExist(msg)
    return PatientIdentifierListSpec.model_construct(**data).to_json()


setattr(get_config, _SWAPPED, True)


def _current():
    from care.emr.models.patient import PatientIdentifierConfigCache

    return PatientIdentifierConfigCache, PatientIdentifierConfigCache.__dict__.get(
        "get_config"
    )


def _forget(schema, keys):
    """Clear the Redis copy of one config (both of CARE's keys) in ``schema``."""
    from care.emr.models.patient import PatientIdentifierConfig
    from care.emr.resources.base import model_cache_key, model_string
    from django.core.cache import cache
    from django_tenants.utils import schema_context

    with schema_context(schema):
        for key in keys:
            if key:
                cache.delete_pattern(
                    model_cache_key(model_string(PatientIdentifierConfig), pk=key)
                )


def _clear_after_commit(sender, instance, **kwargs):
    from django.db import connection, transaction

    keys = (instance.id, getattr(instance, "external_id", None))
    transaction.on_commit(partial(_forget, connection.schema_name, keys))


def install():
    """Swap CARE's lookup (nothing when CARE no longer has one: E025 then refuses)."""
    from care.emr.models.patient import PatientIdentifierConfig
    from django.db.models.signals import post_save

    cache_class, current = _current()
    if current is None:
        return
    post_save.connect(
        _clear_after_commit,
        sender=PatientIdentifierConfig,
        dispatch_uid="ihr_identifier_config_clear_after_commit",
    )
    if getattr(getattr(current, "__func__", None), _SWAPPED, False):
        return
    swapped = classmethod(get_config)
    swapped.ihr_original = current
    cache_class.get_config = swapped


def check():
    """Why the swap is not in place ([] when it is)."""
    _, current = _current()
    if current is None:
        return [
            "CARE's PatientIdentifierConfigCache has no get_config: look at the upstream "
            "change and swap again"
        ]
    if not getattr(getattr(current, "__func__", None), _SWAPPED, False):
        return [
            "PatientIdentifierConfigCache.get_config is CARE's own per-process dict: other "
            "web processes would show a changed config's old display and rules"
        ]
    try:
        from care.emr.resources.patient_identifier.spec import (
            PatientIdentifierListSpec,
        )
    except ImportError:
        return ["CARE's PatientIdentifierListSpec moved: look at the upstream change"]
    if not getattr(PatientIdentifierListSpec, "__cacheable__", False):
        return ["CARE's PatientIdentifierListSpec is no longer cacheable"]
    return []


def system_check(app_configs=None, **kwargs):
    from django.core.checks import Error

    return [
        Error(f"identifier config lookup: {problem}", id="ihr_platform.E025")
        for problem in check()
    ]
