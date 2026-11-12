"""CARE's terminology client, answered in this process (P1-T10a1, ADR-016).

CARE builds ``FHIRClient(settings.SNOWSTORM_DEPLOYMENT_URL)`` and sends every value-set search,
code check and code lookup to that server over HTTP (by default a third party's address over plain
HTTP). ``install()`` replaces the client every CARE resource uses with ``LocalTerminologyClient``,
which answers the same FHIR operations from our own tables, and makes ``FHIRClient.query`` itself
refuse, so no code path left in CARE can send a search outside. Nothing in ``apps/`` is edited.
"""

import logging

from ihr_terminology import engine

log = logging.getLogger("ihr.terminology")


def _outcome(message):
    return {
        "resourceType": "OperationOutcome",
        "issue": [
            {"severity": "error", "code": "not-supported", "diagnostics": message}
        ],
    }


def _params(parameters):
    """FHIR Parameters (a dict with "parameter") -> {name: parameter}"""
    out = {}
    for p in (parameters or {}).get("parameter") or []:
        if isinstance(p, dict) and isinstance(p.get("name"), str):
            out.setdefault(p["name"], p)
    return out


class LocalTerminologyClient:
    server_url = "local:ihr_terminology"

    def query(self, *, method, resource, operation=None, parameters, detail=None):
        try:
            if operation or detail:
                msg = f"operation {operation or detail} not supported"
                raise engine.RefusedError(msg)
            if method == "POST" and resource == "ValueSet/$expand":
                return self._expand(parameters)
            if method == "POST" and resource == "ValueSet/$validate-code":
                return self._validate(parameters)
            if method == "GET" and resource == "CodeSystem/$lookup":
                p = parameters or {}
                return {
                    "resourceType": "Parameters",
                    "parameter": engine.lookup(p.get("system"), p.get("code")),
                }
            if method == "GET" and resource == "CodeSystem":
                return self._code_systems(parameters or {})
            msg = f"{method} {resource} not supported"
            raise engine.RefusedError(msg)
        except engine.RefusedError as e:
            log.info("terminology: refused: %s", e)
            return _outcome(str(e))

    def _expand(self, parameters):
        p = _params(parameters)
        compose = ((p.get("valueSet") or {}).get("resource") or {}).get("compose")
        results = engine.expand(
            compose,
            search=(p.get("filter") or {}).get("valueString", ""),
            count=(p.get("count") or {}).get("valueInteger", 10),
            display_language=(p.get("displayLanguage") or {}).get("valueString"),
            include_designations=bool(
                (p.get("includeDesignations") or {}).get("valueBoolean")
            ),
        )
        return {
            "resourceType": "ValueSet",
            "expansion": {"total": len(results), "contains": results},
        }

    def _validate(self, parameters):
        p = _params(parameters)
        compose = ((p.get("valueSet") or {}).get("resource") or {}).get("compose")
        result, message = engine.validate(
            compose, (p.get("coding") or {}).get("valueCoding")
        )
        out = [{"name": "result", "valueBoolean": result}]
        if message:
            out.append({"name": "message", "valueString": message})
        return {"resourceType": "Parameters", "parameter": out}

    def _code_systems(self, parameters):
        found = engine.code_systems(
            name=parameters.get("name"), url=parameters.get("url")
        )
        return {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": len(found),
            "entry": [
                {
                    "resource": {
                        "resourceType": "CodeSystem",
                        "id": str(v.pk),
                        "url": v.url,
                        "name": v.name or v.url,
                        "title": v.title or v.name or v.url,
                        "version": v.version,
                    }
                }
                for v in found
            ],
        }


LOCAL = LocalTerminologyClient()


def _refuse(self, **kwargs):
    msg = "CARE's remote terminology client is switched off in IndieHR (ihr_terminology, ADR-016)"
    raise RuntimeError(msg)


def _subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


def install():
    """Point every CARE terminology resource at LOCAL; refuse to start if that did not take."""
    from care.emr.fhir import client as care_client
    from care.emr.fhir.resources import base

    base.ResourceManger._fhir_client = LOCAL  # noqa: SLF001 (replacing it is the point)
    base.default_fhir_client = LOCAL
    care_client.FHIRClient.query = _refuse
    for cls in (base.ResourceManger, *_subclasses(base.ResourceManger)):
        if cls._fhir_client is not LOCAL:
            msg = f"ihr_terminology: {cls.__name__} does not use the local terminology client"
            raise RuntimeError(msg)
