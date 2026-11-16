"""Test-only per-facility kinds (P1-T15b3, Q-K70 A): real CARE catalogue models, test handlers.

Registered by ihr_refdata.kinds only while ``IHR_REFDATA_TEST_KINDS`` is on (the test settings;
the deployment settings refuse it, settings/secure.py), so a pack of these kinds is an unknown
kind on a server. No catalogue content ships: these exist to prove the per-facility engine on
CARE's own slug prefix, id arrays and PROTECT.

    t_specimen   SpecimenDefinition: title, status and a minimal type_collected
    t_activity   ActivityDefinition: title, status, a fixed classification, kind and code, and
                 specimen_requirements linking t_specimen items of the same facility
"""

from ihr_refdata.catalogue import Catalogue

SPECIMEN_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0487"
TEST_CODE_SYSTEM = "https://ihr.test/code"  # test packs only


def _coding(value, name):
    if not (
        isinstance(value, dict)
        and set(value) <= {"system", "code", "display"}
        and all(isinstance(value.get(k), str) and value[k] for k in ("system", "code"))
    ):
        msg = f"{name} must be a coding: system and code (and display)"
        raise ValueError(msg)


class TSpecimen(Catalogue):
    KIND = "t_specimen"
    FIELDS = ("title", "status", "type_collected")
    DEFAULTS = {"description": ""}

    @classmethod
    def model(cls):
        from care.emr.models.specimen_definition import SpecimenDefinition

        return SpecimenDefinition

    @classmethod
    def check_fields(cls, item):
        _coding(item["type_collected"], "type_collected")


class TActivity(Catalogue):
    KIND = "t_activity"
    FIELDS = (
        "title",
        "status",
        "classification",
        "kind",
        "code",
        "specimen_requirements",
    )
    LINKS = {"specimen_requirements": "t_specimen"}
    DEFAULTS = {"description": "", "usage": ""}

    @classmethod
    def model(cls):
        from care.emr.models.activity_definition import ActivityDefinition

        return ActivityDefinition

    @classmethod
    def check_fields(cls, item):
        if item["classification"] != "laboratory" or item["kind"] != "service_request":
            msg = "a test activity is a laboratory service_request"
            raise ValueError(msg)
        _coding(item["code"], "code")


KINDS = (TSpecimen, TActivity)


def specimen(key, *, title=None, status="active", code="BLD"):
    """A t_specimen pack item."""
    return {
        "slug": key,
        "title": title or f"Test specimen {key}",
        "status": status,
        "type_collected": {"system": SPECIMEN_SYSTEM, "code": code, "display": code},
    }


def activity(key, *specimens, title=None, status="active"):
    """A t_activity pack item linking ``specimens`` (t_specimen keys) in each facility."""
    return {
        "slug": key,
        "title": title or f"Test activity {key}",
        "status": status,
        "classification": "laboratory",
        "kind": "service_request",
        "code": {"system": TEST_CODE_SYSTEM, "code": key, "display": key},
        "specimen_requirements": list(specimens),
    }
