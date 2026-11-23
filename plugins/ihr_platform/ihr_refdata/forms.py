"""The "questionnaire" kind: pack forms (P1-T15b2b; docs/reviews/p1-t15b/design.md §3.3 and
"After the attack" items 1-3 and 12).

A pack item is ``{slug: <key>, form_version, title, description, subject_type, status,
styling_metadata, questions}``. A new form_version is a new form: CARE's row for version N has
the slug ``ihr-pack-<key>-v<N>`` (a reserved namespace: the guard refuses any other writer, see
forms_guard.py), ``version = str(N)``, and ``PackItem.target`` names it. An answered form is
never edited in place (the guard refuses it in the database), so:

- the first version is created and linked to the hospital: one ``QuestionnaireOrganization``
  per top-level organisation (no parent, active, not deleted, not a government one), so the
  hospital's admins see it and widen or narrow its links through CARE's own
  ``set_organizations`` (item 1, Q-K80 A); the change list says ``linked-to-hospital`` and
  how many. A hospital with no such organisation (a provisioned one has only government
  organisations today) gets no unlinked form nobody could see: the item waits as
  ``missing-link`` and every apply asks again (Q-K68 A; reviews E1, E2);
- a newer version is a new row: the previous version's organisation and facility-organisation
  links are copied with ``.save()`` (CARE's caches sync), and the previous row is retired by
  status; its content and its answers stay as they were. The change list and the item detail
  count what still points at the previous row (drafts, response templates; item 12);
- the same form_version only undeletes the row and sets its status (never its content).
  When the row's content is no longer the pack's (edited in the shell before it had
  answers), accepting the pack's makes a new row as a newer version does: a recorded hash is
  always one the row holds (review E3).

The engine adopts only the row its own record names (``rows`` resolves by the record's
target). A slug already taken by a row the record does not name (made before P1-T15b2b, or in
the shell; deleted ones count: the slug index covers them) is never adopted: the next free
``-2`` ... ``-9`` suffix is used and recorded; ten taken slugs are a ``conflict`` (item 3).
A deleted row is the hospital's choice (``RESTORE_DELETED`` False).
"""

import hashlib
import json
import re
import uuid

from django.db import connection

KIND = "questionnaire"
SCOPE = "hospital"
RESTORE_DELETED = False  # a deleted form is the hospital's choice (Q-K63)
FIELDS = (
    "form_version",
    "title",
    "description",
    "subject_type",
    "status",
    "styling_metadata",
    "questions",
)
ITEM_KEYS = frozenset(("slug", *FIELDS))
PREFIX = "ihr-pack-"
# CARE's slug pattern, lower case, 2-30 characters: "ihr-pack-<key>-v999-9" fits CARE's 5-50
KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,28}[a-z0-9]$")
MAX_FORM_VERSION = 999
MAX_TITLE = (
    255  # CARE's Questionnaire.title (review E5: refused fleet-wide, not per hospital)
)
SUFFIXES = ("", *(f"-{n}" for n in range(2, 10)))  # ten candidate slugs per version
STATUSES = ("active", "draft")  # what a pack ships; CARE's "retired" is the removal
SUBJECTS = ("patient", "encounter")
RETIRED = "retired"
GOVT = "govt"  # CARE's government organisations (states, districts): never linked
WRITE_SETTING = (
    "ihr.pack_write"  # the guard lets a reserved slug through only with it on
)


def _models():
    from care.emr.models.questionnaire import (
        FormSubmission,
        Questionnaire,
        QuestionnaireFacilityOrganization,
        QuestionnaireOrganization,
        QuestionnaireResponseTemplate,
    )

    return {
        "form": Questionnaire,
        "org_link": QuestionnaireOrganization,
        "facility_link": QuestionnaireFacilityOrganization,
        "submission": FormSubmission,
        "template": QuestionnaireResponseTemplate,
    }


def _hash(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def row_version(row):
    """The row's CARE ``version`` as a form_version (None when it is not one of ours)."""
    text = row.version if isinstance(row.version, str) else ""
    return int(text) if text.isdigit() and len(text) <= 3 else None  # noqa: PLR2004


def item_fields(item):
    return {name: item[name] for name in FIELDS}


def row_fields(row):
    version = row_version(row)
    return {
        "form_version": version if version is not None else row.version,
        "title": row.title,
        "description": row.description,
        "subject_type": row.subject_type,
        "status": row.status,
        "styling_metadata": row.styling_metadata,
        "questions": row.questions,
    }


# --- the pack item ------------------------------------------------------------------------


def _questions(questions, seen_links, seen_ids, depth=0):
    if not isinstance(questions, list):
        msg = "questions must be a list"
        raise TypeError(msg)
    if depth > 10:  # noqa: PLR2004
        msg = "questions are nested more than 10 deep"
        raise ValueError(msg)
    for question in questions:
        if not isinstance(question, dict):
            msg = "each question must be an object"
            raise TypeError(msg)
        for name in ("link_id", "id", "text", "type"):
            if not isinstance(question.get(name), str) or not question[name]:
                msg = f"each question needs a non-empty string {name}"
                raise TypeError(msg)
        # CARE keys answers by the canonical text of a UUID4 or UUID5 (review E4)
        parsed = uuid.UUID(question["id"])  # ValueError when it is not one
        if str(parsed) != question["id"] or parsed.version not in (4, 5):
            msg = (
                f"question id {question['id']!r} must be a lower-case, hyphenated UUID "
                "of version 4 or 5"
            )
            raise ValueError(msg)
        if question["link_id"] in seen_links:
            msg = f"link_id {question['link_id']!r} is used twice"
            raise ValueError(msg)
        if question["id"] in seen_ids:
            msg = f"question id {question['id']!r} is used twice"
            raise ValueError(msg)
        seen_links.add(question["link_id"])
        seen_ids.add(question["id"])
        if "questions" in question:
            _questions(question["questions"], seen_links, seen_ids, depth + 1)


def check_item(item):
    """Shape only, no database (ValueError/TypeError/KeyError otherwise): the fields and their
    types, the key, the form_version, and unique ``link_id`` and ``id`` across the nested tree.
    CARE's own validation (coded fields may read value sets) runs in a test hospital in
    ``ihr_refdata.tests.test_forms`` over every committed form pack."""
    if not isinstance(item, dict):
        msg = "not an object"
        raise TypeError(msg)
    if missing := sorted(ITEM_KEYS - set(item)):
        msg = f"missing {', '.join(missing)}"
        raise KeyError(msg)
    if extra := sorted(set(item) - ITEM_KEYS):
        msg = f"unknown fields {', '.join(extra)}"
        raise ValueError(msg)
    if not isinstance(item["slug"], str) or not KEY.match(item["slug"]):
        msg = (
            "the key must be 2-30 characters of lower-case letters, digits, '-' or '_', "
            "starting and ending with a letter or digit"
        )
        raise ValueError(msg)
    version = item["form_version"]
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or not 1 <= version <= MAX_FORM_VERSION
    ):
        msg = f"form_version must be a whole number from 1 to {MAX_FORM_VERSION}"
        raise ValueError(msg)
    if not isinstance(item["title"], str) or not item["title"].strip():
        msg = "title must be a non-empty string"
        raise TypeError(msg)
    if len(item["title"]) > MAX_TITLE:
        msg = f"title must be at most {MAX_TITLE} characters"
        raise ValueError(msg)
    if not isinstance(item["description"], str):
        msg = "description must be a string"
        raise TypeError(msg)
    if item["subject_type"] not in SUBJECTS:
        msg = f"subject_type must be one of {', '.join(SUBJECTS)}"
        raise ValueError(msg)
    if item["status"] not in STATUSES:
        msg = f"status must be one of {', '.join(STATUSES)}"
        raise ValueError(msg)
    if not isinstance(item["styling_metadata"], dict):
        msg = "styling_metadata must be an object"
        raise TypeError(msg)
    if not item["questions"]:
        msg = "a form needs at least one question"
        raise ValueError(msg)
    _questions(item["questions"], set(), set())


def check_release(versions):
    """Across every release of one pack (ValueError: the engine makes it a fleet-wide
    PackError): one ``(key, form_version)`` always has the same content, and a key's
    form_version never goes down. A content change comes with a new form_version."""
    seen, newest = {}, {}
    for release in versions:
        for item in release.items:
            if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
                continue  # check_item names it
            key, version = item["slug"], item.get("form_version")
            content = item_hash(item)
            known = seen.setdefault((key, version), (content, release.version))
            if known[0] != content:
                msg = (
                    f"form {key!r} v{version} has other content in release v{release.version} "
                    f"than in v{known[1]}: a content change needs a new form_version"
                )
                raise ValueError(msg)
            if isinstance(version, int) and key in newest and version < newest[key][0]:
                msg = (
                    f"form {key!r} goes from form_version {newest[key][0]} (release "
                    f"v{newest[key][1]}) down to {version} (release v{release.version})"
                )
                raise ValueError(msg)
            if isinstance(version, int):
                newest[key] = (version, release.version)


def item_hash(item):
    return _hash(item_fields(item))


def row_hash(row):
    return _hash(row_fields(row))


def retired_hash(row):
    return _hash({**row_fields(row), "status": RETIRED})


def decision_hash(row):
    """What the admin decides on: the content, the slug and whether it is soft-deleted."""
    return _hash(
        {"fields": row_fields(row), "slug": row.slug, "deleted": bool(row.deleted)}
    )


def is_retired(row):
    return row.status == RETIRED


def target(item):
    """The CARE slug of the item's form_version (before any suffix)."""
    return f"{PREFIX}{item['slug']}-v{item['form_version']}"


def record_target(item, row):
    """What ``PackItem.target`` records once ``row`` holds the item: the row's own slug (it
    may carry a suffix)."""
    return row.slug if row is not None else target(item)


def candidates(item):
    base = target(item)
    return [base + suffix for suffix in SUFFIXES]


def _taken(slugs):
    """The slugs (lower case) some row holds in any case, deleted rows included."""
    from django.db.models.functions import Lower

    form = _models()["form"]
    wanted = [s.lower() for s in slugs]
    return set(
        form._base_manager.annotate(ihr_slug=Lower("slug"))  # noqa: SLF001
        .filter(ihr_slug__in=wanted)
        .values_list("ihr_slug", flat=True)
    )


def free_slug(item):
    """The first of the item's ten candidate slugs no row holds (None when all are taken)."""
    options = candidates(item)
    taken = _taken(options)
    return next((s for s in options if s not in taken), None)


# --- reading the hospital's rows ----------------------------------------------------------


def _live_forms():
    """The forms the pack writes and reads: never CARE 3.1's archive copies of an earlier
    revision, which share the live form's slug (``latest_revision`` names it, P2-T0a F3), and
    only the hospital-wide ones, as value sets are (a form moved to one facility, an
    organisation or a user is no longer the pack's, review P2-T0a D4)."""
    form = _models()["form"]
    return form._base_manager.filter(  # noqa: SLF001
        latest_revision__isnull=True,
        auth_context="instance",
        facility__isnull=True,
        facility_organization__isnull=True,
    )


def rows(keys, *, lock=False, targets=None):
    """{key: row}: only the row a record names (``targets``: {key: the record's target}),
    soft-deleted ones included, archive copies never. A key without a recorded target has no
    row here: a row the pack never wrote is never adopted (item 3). A live row is preferred
    to a deleted one of the same slug, then the oldest (the pick never depends on order)."""
    targets = {k: t for k, t in (targets or {}).items() if k in set(keys) and t}
    if not targets:
        return {}
    found = _live_forms().filter(slug__in=list(targets.values()))
    found = found.order_by("slug", "deleted", "pk")
    if lock:
        # FOR NO KEY UPDATE: the answers' foreign keys only take FOR KEY SHARE; an answer
        # being inserted (FOR SHARE, the guard) waits for this transaction, as it must
        found = found.select_for_update(no_key=True)
    by_slug = {}
    for row in found:
        by_slug.setdefault(row.slug, row)
    return {k: by_slug[t] for k, t in targets.items() if t in by_slug}


def all_present(keys, targets=None):
    """True when every key's recorded row exists in any state (item 11: a deleted one is the
    hospital's own until the pack changes the form)."""
    keys = set(keys)
    targets = targets or {}
    if any(not targets.get(k) for k in keys):
        return False
    wanted = {targets[k] for k in keys}
    present = (
        _live_forms().filter(slug__in=list(wanted)).values("slug").distinct().count()
    )
    return present == len(wanted)


def same_content(row, item):
    """The row holds the item's content (its status and soft delete aside)."""
    held, wanted = row_fields(row), item_fields(item)
    held.pop("status")
    wanted.pop("status")
    return held == wanted


def needs_new_row(row, item):
    """A write of ``item`` over ``row`` makes a new row: a newer form_version, or the same one
    whose content the row no longer holds (review E3). Never for an older one."""
    have = row_version(row)
    if have is None or item["form_version"] > have:
        return True
    return item["form_version"] == have and not same_content(row, item)


def conflict(key, row, item):
    """Why nothing can be written ("" when nothing blocks): a new row is needed (none yet, a
    newer form_version, or the pack's content back over an edited one) and every one of its
    ten candidate slugs is taken."""
    if item is None:
        return ""
    if row is not None and not needs_new_row(row, item):
        return ""
    if free_slug(item) is None:
        return (
            f"the ten slugs {target(item)}, {target(item)}-2 ... -9 are all taken by forms "
            "the pack did not write"
        )
    return ""


# --- what the admin is shown --------------------------------------------------------------


def label(row, item):
    if row is not None and row.title:
        return row.title
    return (item or {}).get("title", "")


def effect(row, item):
    if item is None:
        return "retire"
    if row is None:
        return "create"
    if needs_new_row(row, item):
        return "new-version"
    return "restore" if row.deleted else "status-only"


def shown(row=None, item=None, *, retire=False):
    if row is None and item is None:
        return None
    fields = dict(row_fields(row) if item is None else item_fields(item))
    if retire:
        fields["status"] = RETIRED
    return fields


def stranded(row):
    """What still points at a previous version once a newer one replaces it (item 12, counts
    only): draft submissions on it and response templates keyed to it."""
    models = _models()
    return {
        "draft_submissions": models["submission"]
        .objects.filter(questionnaire=row, status="draft")
        .count(),
        "response_templates": models["template"]
        .objects.filter(questionnaire=row)
        .count(),
    }


def notes(row, item):
    """Extra facts for the item detail: what a new version would leave on the current one."""
    if row is None or item is None or not needs_new_row(row, item):
        return {}
    return {"previous": row.slug, "stranded": stranded(row)}


# --- writes (always .save(): CARE's link caches sync) -------------------------------------


class _PackWrite:
    """``ihr.pack_write`` on for one write in this transaction (the guard reads it)."""

    def __enter__(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config(%s, 'on', true)", [WRITE_SETTING])

    def __exit__(self, *exc):
        if exc[0] is None:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config(%s, 'off', true)", [WRITE_SETTING])
        return False


def missing_link(key, row, item):
    """Why a first version cannot be written yet ("" when it can): no organisation to link it
    to, so nobody but a superuser could see it (Q-K68 A: pending ``missing-link``, asked again
    at every apply; reviews E1, E2). A newer version copies the previous one's links."""
    if item is None or row is not None or hospital_organisations():
        return ""
    return (
        "the hospital has no active top-level organisation that is not a government one to "
        "link the form to (Q-K80)"
    )


def linkable(items):
    """Item 11 (P1-T15b3): a first version held for a missing link can be written once the
    hospital has an organisation to link it to (one query); until then the start-up
    short-circuit does not reconcile for it."""
    return _organisation_exists()


def _organisation_exists():
    from care.emr.models.organization import Organization

    return (
        Organization.objects.filter(parent__isnull=True, active=True)
        .exclude(org_type=GOVT)
        .exists()
    )


def hospital_organisations():
    """The hospital's top-level organisations a first version is linked to: no parent,
    active, not deleted, not a government one (Q-K80 A: the 36 state organisations are not)."""
    from care.emr.models.organization import Organization

    return list(
        Organization.objects.filter(parent__isnull=True, active=True)
        .exclude(org_type=GOVT)
        .order_by("id")
    )


def _new_row(item):
    """A new CARE row for ``item`` at its first free slug (ValueError when none is free:
    ``conflict`` refused it before)."""
    slug = free_slug(item)
    if slug is None:
        raise ValueError(conflict(item["slug"], None, item))
    form = _models()["form"]
    row = form(
        slug=slug,
        version=str(item["form_version"]),
        title=item["title"],
        description=item["description"],
        subject_type=item["subject_type"],
        status=item["status"],
        styling_metadata=item["styling_metadata"],
        questions=item["questions"],
        # a hospital-wide form, with the hash CARE 3.1 keeps of its questions: CARE's own
        # update compares it to tell a revision from a status edit (P2-T0a F3)
        auth_context="instance",
        questions_hash=questions_hash(item["questions"]),
    )
    with _PackWrite():
        row.save()
    return row


def questions_hash(questions):
    """What CARE 3.1 stores in ``questions_hash`` (QuestionnaireCreateSpec and emr migration
    0084): the sha256 of the questions' JSON with sorted keys."""
    text = json.dumps(questions, sort_keys=True).encode("utf-8")
    return hashlib.sha256(text).hexdigest()


def _sync_caches(row):
    """CARE 3.1 no longer fills a form's link caches when a link is saved: its views call
    these after writing links, and so do we (organisation and facility-organisation)."""
    row.sync_org_cache()
    row.sync_facility_org_cache()


def create(item):
    """The first version: a new row, linked to the hospital (item 1). Never a form with no
    link (``missing_link`` held it before; ValueError if the organisations went meanwhile)."""
    organisations = hospital_organisations()
    if not organisations:
        raise ValueError(missing_link(item["slug"], None, item))
    row = _new_row(item)
    org_link = _models()["org_link"]
    linked = 0
    for organisation in organisations:
        org_link(questionnaire=row, organization=organisation).save()
        linked += 1
    _sync_caches(row)
    row.ihr_change = {
        "target": row.slug,
        "links": "linked-to-hospital",
        "linked": linked,
    }
    return row


def write(row, item, *, restore=False):
    """The same form_version holding the pack's content: undelete (``restore``) and the pack's
    status only. A newer one, or the same one whose content was edited (review E3): a new row
    with the previous one's links copied, then the previous row retired (its content and
    answers untouched)."""
    have = row_version(row)
    wanted = item["form_version"]
    if have is not None and wanted < have:
        msg = f"form_version {wanted} is older than the row's {have}"
        raise ValueError(msg)
    if not needs_new_row(row, item):
        changed = []
        if restore and row.deleted:
            row.deleted = False
            changed.append("deleted")
        if row.status != item["status"]:
            row.status = item["status"]
            changed.append("status")
        if changed:
            row.save(update_fields=[*changed, "modified_date"])
        row.ihr_change = {"target": row.slug}
        return row
    models = _models()
    new = _new_row(item)
    copied = 0
    for name in ("org_link", "facility_link"):
        model = models[name]
        for link in model.objects.filter(questionnaire=row).order_by("id"):
            model(questionnaire=new, organization_id=link.organization_id).save()
            copied += 1
    _sync_caches(new)
    left = stranded(row)
    retire(row)
    new.ihr_change = {
        "target": new.slug,
        "previous": row.slug,
        "links": "copied",
        "copied": copied,
        "stranded": left,
    }
    return new


def retire(row):
    """A form the pack no longer ships (or an older version): retired by status only."""
    if row.status != RETIRED:
        row.status = RETIRED
        row.save(update_fields=["status", "modified_date"])
    return row


def change(row):
    """What the change list says beside the change itself (target, links, what was left)."""
    return dict(getattr(row, "ihr_change", None) or {})
