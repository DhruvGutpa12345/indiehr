"""An answered form is never edited in place, and answers are never hard-deleted (P1-T15b2b;
docs/reviews/p1-t15b/design.md §3.4 and "After the attack" items 3-5; Q-K66, Q-K72).

Two trigger functions in each hospital schema, installed like the role placement guard
(ihr_access/placement.py): owner-owned, ``REVOKE ALL FROM PUBLIC``, kept current by
post_migrate (never by re-running migrations), stamped with this build's fingerprint
(ihr_platform/db_functions.py), checked by ``problems()`` (the fleet's check and repair) and
``runtime_problems_by_schema()`` (db_roles' "controls" gate: a hospital whose guard is broken is
not served). They are triggers, not signals or a viewset swap, because CARE's API, the shell,
raw SQL as the app role and our own engine all write these tables.

``ihr_questionnaire_guard`` on ``emr_questionnaire``, BEFORE INSERT, UPDATE OF the frozen
columns, and DELETE:

- a frozen column (``FROZEN``) changed while an answer exists (a row of any of
  ``ANSWER_TABLES`` naming the form, soft-deleted ones too): ``ihr_forms: form <slug> has
  answers; make a new version instead``;
- a hard delete of a form with answers (CASCADE would delete patients' answers);
- an INSERT, or an UPDATE of ``slug``, into the reserved ``ihr-pack-`` namespace (any case)
  unless the transaction set ``ihr.pack_write = on`` (only the engine's form writes do:
  forms.py). CARE's create then answers an error, never a stolen pack slug. The one INSERT
  allowed without it is CARE 3.1's archive copy of a live form at that very slug (its
  ``latest_revision`` names it: CARE's in-place revision, P2-T0a F3), never a live form; a
  reserved row's ``latest_revision`` changes only with the setting.

``ihr_questionnaire_answer_guard`` on every answer table (``ANSWER_TABLES``: CARE 3.1's
facility-resource and referral answers too, P2-T0a F2), BEFORE INSERT, UPDATE OF
``questionnaire_id``, and DELETE:

- every hard delete is refused (a soft delete through ``deleted`` is free). Django's cascade
  deletes answers before their form, so the form-side check alone would see none;
- an UPDATE that moves an answer to another form (or to none) is refused: CARE sets the link
  only on create, so "detach, edit, reattach" is impossible;
- an INSERT takes ``FOR SHARE`` on the form row first. An UPDATE of the form takes FOR NO KEY
  UPDATE, which a foreign key's FOR KEY SHARE does not conflict with; FOR SHARE does, so an
  answer being inserted and a frozen column being changed wait for each other, and the guard's
  check always sees the committed answer (the race of §3.4).

A frozen change, or a hard delete of a form, is refused unless the transaction is READ
COMMITTED: under REPEATABLE READ or SERIALIZABLE the answer check reads the transaction's
snapshot and would miss an answer committed after it (review G1; SSI cannot help, CARE's answer
insert is READ COMMITTED).

The reserved namespace goes both ways (review E6): a slug moved out of ``ihr-pack-``, or a hard
delete of a row in it, also needs ``ihr.pack_write``, so a pack's slug is never freed for
another pack's record to take.

CARE 3.1 resolves an answer's questions by the pair (form, revision): the answer names the live
form and records the ``internal_revision`` it was given on, and the questions of an older
revision live only in CARE's archive copy of the form (``latest_revision`` names the live
row). So (P2-T0a review D1, D2):

- an UPDATE of an answer's revision (``ANSWER_REVISION``: ``revision``, or
  ``questionnaire_revision`` on a referral's answer) to a value other than its form's current
  ``internal_revision``, read ``FOR SHARE``, is refused: CARE inserts an answer at 1 and then
  sets the form's current revision, which passes; pointing an answer at another revision's
  questions never does. A write that leaves the value as it is passes;
- an archive copy is "answered" when an answer names its live form at the copy's own
  ``internal_revision``: its frozen columns and ``deleted`` (otherwise free) never change and it
  is never hard-deleted, like a live form with answers. A submit that read the form just
  before an edit of it committed records such an answer (CARE's own race); the copy that holds
  the questions the clinician saw stays as it was.

TRUNCATE of any answer table (directly, or reached by ``TRUNCATE ... CASCADE`` from a patient
or a visit) is refused by a BEFORE TRUNCATE statement trigger unless the session set
``ihr.answers_truncate = on`` (only the test runner's flush between CARE's TransactionTestCases
does; review G2). The app role has no TRUNCATE at all; this stops an accidental owner shell.

Hard deletes of answers are refused for every role, a SECURITY DEFINER function included: a
trigger fires whoever runs the statement. An erasure path (P4-T1, Q-K66), if one is ever
needed, needs either a bypass this trigger itself checks (a new guard version) or redaction of
the answer's content in place (``responses`` and ``structured_responses`` are not frozen).
Hard deletes of a patient or a visit that has answers are refused too (Django's cascade
reaches the answers first). ``DROP SCHEMA`` (offboarding, a restore's park) fires no row
trigger. A refusal is SQLSTATE 23514 (Django's IntegrityError); CARE's EMR views answer it 400
(ihr_access/exceptions.py).
"""

import re

from django.db import transaction
from ihr_platform import db_functions
from psycopg import sql

FORM_FUNCTION = "ihr_questionnaire_guard"
ANSWER_FUNCTION = "ihr_questionnaire_answer_guard"
FUNCTIONS = (FORM_FUNCTION, ANSWER_FUNCTION)  # the first is the stamp's anchor
TRUNCATE_TRIGGER = "ihr_questionnaire_answer_no_truncate"  # runs ANSWER_FUNCTION
FORM_TABLE = "emr_questionnaire"
# CARE 3.1 adds two more (review P2-T0a F2): answers about a facility's resources, and the
# answers given with a patient's referral; each names its form in ``questionnaire_id``
ANSWER_TABLES = (
    "emr_questionnaireresponse",
    "emr_formsubmission",
    "emr_facilityresourcequestionnaireresponse",
    "emr_referralquestionnaireresponse",
)
# The column of each answer table that records the form revision the answer was given on
# (None: the table has none; a form submission names no revision)
ANSWER_REVISION = {
    "emr_questionnaireresponse": "revision",
    "emr_formsubmission": None,
    "emr_facilityresourcequestionnaireresponse": "revision",
    "emr_referralquestionnaireresponse": "questionnaire_revision",
}
PREFIX = "ihr_forms:"
WRITE_SETTING = "ihr.pack_write"
TRUNCATE_SETTING = "ihr.answers_truncate"
RESERVED = "ihr-pack-"
# The first ihr_refdata migration that installs the guard: a schema that has it must hold one
FIRST_MIGRATION = "0004_forms_guard"
# Every column of emr_questionnaire is one or the other (tests.test_forms classifies them, so
# a column upstream adds fails until it is listed here). Frozen: what an answer was given
# against. Free: what an answered form may still change (status, the link caches, soft
# delete, audit fields; created_by goes NULL when a user is deleted).
#
# CARE 3.1 (review P2-T0a F3) revises a form in place: an archive copy of the old row (its
# ``latest_revision`` names the live row) is inserted, then the live row's questions are
# edited and its ``internal_revision`` goes up; an answer resolves its questions by the
# revision it records. The guard keeps refusing that edit of a form with answers (an answer
# stays with the exact row it was given on: docs/QUESTIONS.md, P2-T0a).
FROZEN = (
    "id",
    "external_id",
    "created_date",
    "slug",
    "version",
    "title",
    "description",
    "subject_type",
    "styling_metadata",
    "questions",
    # the revision an answer records and resolves its questions by
    "internal_revision",
    # set only on CARE's archive copies, when they are inserted: an answered form set to it
    # would read as a past revision (its answers listed under another form, new ones refused)
    "latest_revision_id",
    # whose form it is (instance, facility, facility organisation, user): an answered form
    # never moves to another owner or another facility
    "auth_context",
    "facility_id",
    "facility_organization_id",
)
FREE = (
    "status",
    "organization_cache",
    "internal_organization_cache",
    "deleted",
    "modified_date",
    "updated_by_id",
    "created_by_id",
    "history",
    "meta",
    # a cache of ``questions`` (frozen) CARE compares to tell a revision from another edit;
    # emr migration 0084 fills it on every form, answered ones too
    "questions_hash",
    # what CARE's action engine runs on a later submit: no answer given depends on it
    "actions",
)
# Every foreign key that points at a form or an answer, classified (review Q5: a table
# upstream adds that points at one fails tests.test_forms until it is listed here). "answer":
# guarded as an answer (ANSWER_TABLES); the others are not answers, with why.
REFERENCES = {
    "emr_questionnaireresponse.questionnaire_id": "answer",
    "emr_formsubmission.questionnaire_id": "answer",
    "emr_questionnaireresponse.form_submission_id": "answer (an answer of a submission)",
    "emr_questionnaireorganization.questionnaire_id": "link (free; copied to a new version)",
    "emr_questionnairefacilityorganization.questionnaire_id": (
        "link (free; copied to a new version)"
    ),
    "emr_questionnaireresponsetemplate.questionnaire_id": (
        "template (a pre-fill, not an answer; counted as stranded, item 12)"
    ),
    "emr_observation.questionnaire_response_id": (
        "observation (derived from an answer, which is never deleted)"
    ),
    # CARE 3.1 (review P2-T0a F2, F3)
    "emr_facilityresourcequestionnaireresponse.questionnaire_id": "answer",
    "emr_referralquestionnaireresponse.questionnaire_id": "answer",
    "emr_questionnaire.latest_revision_id": (
        "revision (CARE's archive copy names its live form; frozen, and in the reserved "
        "namespace only an archive copy of the same pack form)"
    ),
    "emr_referralavailability_questionnaires.questionnaire_id": (
        "link (a referral availability offers the form; not an answer)"
    ),
    "emr_facilityresourceobservation.questionnaire_response_id": (
        "observation (derived from a facility-resource answer, which is never deleted)"
    ),
}

_FORM_SQL = """
CREATE OR REPLACE FUNCTION {schema}.ihr_questionnaire_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    may_name boolean := coalesce(current_setting('ihr.pack_write', true), '') = 'on';
    committed_only boolean := current_setting('transaction_isolation') = 'read committed';
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF left(lower(OLD.slug), 9) = 'ihr-pack-' AND NOT may_name THEN
            RAISE EXCEPTION 'ihr_forms: form % belongs to a reference pack; retire it',
                OLD.slug USING ERRCODE = 'check_violation';
        END IF;
        IF NOT committed_only THEN
            RAISE EXCEPTION 'ihr_forms: deleting form % needs READ COMMITTED', OLD.slug
                USING ERRCODE = 'check_violation';
        END IF;
        IF {has_answers} THEN
            RAISE EXCEPTION 'ihr_forms: form % has answers; it is never deleted (retire it)',
                OLD.slug USING ERRCODE = 'check_violation';
        END IF;
        IF OLD.latest_revision_id IS NOT NULL AND ({archive_answers}) THEN
            RAISE EXCEPTION 'ihr_forms: form % is the archive of a revision that has answers; '
                'it is never deleted', OLD.slug USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF left(lower(NEW.slug), 9) = 'ihr-pack-' AND NOT may_name THEN
            -- CARE 3.1's archive copy of a live pack form, at the very same slug, takes
            -- nothing from the pack: it is never a live form (P2-T0a F3)
            IF NEW.latest_revision_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM {schema}.emr_questionnaire h
                WHERE h.id = NEW.latest_revision_id AND h.slug = NEW.slug
                  AND h.latest_revision_id IS NULL) THEN
                RAISE EXCEPTION 'ihr_forms: slugs starting ihr-pack- belong to reference packs'
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.slug IS DISTINCT FROM OLD.slug AND NOT may_name
       AND (left(lower(NEW.slug), 9) = 'ihr-pack-' OR left(lower(OLD.slug), 9) = 'ihr-pack-')
    THEN
        RAISE EXCEPTION 'ihr_forms: slugs starting ihr-pack- belong to reference packs'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.latest_revision_id IS DISTINCT FROM OLD.latest_revision_id AND NOT may_name
       AND left(lower(OLD.slug), 9) = 'ihr-pack-' THEN
        RAISE EXCEPTION 'ihr_forms: form % belongs to a reference pack; it stays as it is',
            OLD.slug USING ERRCODE = 'check_violation';
    END IF;
    IF ({new_frozen}) IS DISTINCT FROM ({old_frozen})
       OR (OLD.latest_revision_id IS NOT NULL AND NEW.deleted IS DISTINCT FROM OLD.deleted)
    THEN
        IF NOT committed_only THEN
            RAISE EXCEPTION 'ihr_forms: changing form % needs READ COMMITTED', OLD.slug
                USING ERRCODE = 'check_violation';
        END IF;
        IF ({new_frozen}) IS DISTINCT FROM ({old_frozen})
           AND ({has_answers}) THEN
            RAISE EXCEPTION 'ihr_forms: form % has answers; make a new version instead',
                OLD.slug USING ERRCODE = 'check_violation';
        END IF;
        IF OLD.latest_revision_id IS NOT NULL AND ({archive_answers}) THEN
            RAISE EXCEPTION 'ihr_forms: form % is the archive of a revision that has answers; '
                'it stays as it is', OLD.slug USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END $f$;
REVOKE ALL ON FUNCTION {schema}.ihr_questionnaire_guard() FROM PUBLIC;
"""
_ANSWER_SQL = """
CREATE OR REPLACE FUNCTION {schema}.ihr_questionnaire_answer_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    new_revision text;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        IF coalesce(current_setting('ihr.answers_truncate', true), '') <> 'on' THEN
            RAISE EXCEPTION 'ihr_forms: answers are never deleted (TRUNCATE %)',
                TG_TABLE_NAME USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'ihr_forms: answers are never deleted (% %); mark them deleted',
            TG_TABLE_NAME, OLD.id USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.questionnaire_id IS DISTINCT FROM OLD.questionnaire_id THEN
            RAISE EXCEPTION 'ihr_forms: an answer stays with the form it was given on (% %)',
                TG_TABLE_NAME, OLD.id USING ERRCODE = 'check_violation';
        END IF;
        -- the table's revision column: CARE's answers use ``revision``, a referral's
        -- ``questionnaire_revision``, a form submission has none (both read NULL)
        new_revision := coalesce(to_jsonb(NEW) ->> 'revision',
                                 to_jsonb(NEW) ->> 'questionnaire_revision');
        IF new_revision IS DISTINCT FROM coalesce(to_jsonb(OLD) ->> 'revision',
                                                  to_jsonb(OLD) ->> 'questionnaire_revision')
           AND NEW.questionnaire_id IS NOT NULL THEN
            PERFORM 1 FROM {schema}.emr_questionnaire q WHERE q.id = NEW.questionnaire_id
                AND q.internal_revision::text = new_revision FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'ihr_forms: an answer''s revision moves only to its form''s '
                    'current revision (% %)', TG_TABLE_NAME, OLD.id
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.questionnaire_id IS NOT NULL THEN
        PERFORM 1 FROM {schema}.emr_questionnaire q WHERE q.id = NEW.questionnaire_id
            FOR SHARE;
    END IF;
    RETURN NEW;
END $f$;
REVOKE ALL ON FUNCTION {schema}.ihr_questionnaire_answer_guard() FROM PUBLIC;
"""
# What pg_proc holds for each function besides its body (the definition check, review G3)
CONFIG = ["search_path=pg_catalog, pg_temp"]
# pg_trigger.tgtype bits: ROW 1 | BEFORE 2 | INSERT 4 | DELETE 8 | UPDATE 16 | TRUNCATE 32
TRIGGER_TYPE = 1 | 2 | 4 | 8 | 16
TRUNCATE_TYPE = 2 | 32  # BEFORE TRUNCATE, FOR EACH STATEMENT
# {table: (trigger and function name, the UPDATE OF columns)}: the row triggers
# the form trigger also fires on ``deleted``: free on a live form, frozen on an archive copy
# that holds the questions of answers (D2); an answer trigger fires on the columns that name
# its form and the revision of it
TRIGGERS = {
    FORM_TABLE: (FORM_FUNCTION, (*FROZEN, "deleted")),
    **{
        table: (
            ANSWER_FUNCTION,
            ("questionnaire_id", *((column,) if column else ())),
        )
        for table, column in ANSWER_REVISION.items()
    },
}
TRIGGER_SQL = (
    "CREATE OR REPLACE TRIGGER {name} BEFORE INSERT OR UPDATE OF {columns} OR DELETE "
    "ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()"
)
TRUNCATE_SQL = (
    "CREATE OR REPLACE TRIGGER {name} BEFORE TRUNCATE ON {table} "
    "FOR EACH STATEMENT EXECUTE FUNCTION {function}()"
)
# Every trigger of ours: (table, trigger name, function, tgtype, sorted UPDATE OF columns)
EXPECTED = (
    *(
        (table, function, function, TRIGGER_TYPE, tuple(sorted(columns)))
        for table, (function, columns) in TRIGGERS.items()
    ),
    *(
        (table, TRUNCATE_TRIGGER, ANSWER_FUNCTION, TRUNCATE_TYPE, ())
        for table in ANSWER_TABLES
    ),
)
TRIGGER_NAMES = tuple(sorted({name for _t, name, *_ in EXPECTED}))
VERSION = db_functions.version(
    _FORM_SQL,
    _ANSWER_SQL,
    TRIGGER_SQL,
    TRUNCATE_SQL,
    repr(FROZEN),
    repr(sorted(TRIGGERS.items())),
    repr(EXPECTED),
    repr(ANSWER_TABLES),
    repr(sorted(ANSWER_REVISION.items())),
)
# The tables the guard functions read and guard (the form and every answer table)
TABLES = (FORM_TABLE, *ANSWER_TABLES)
_BODY = re.compile(r"AS \$f\$(.*?)\$f\$;", re.DOTALL)


def fingerprint():
    """What the guard is in this build. Existing hospitals get a changed guard only from
    post_migrate, which the fleet runs only for a hospital with a migration new to it: a
    change here must come with an ihr_refdata migration (no operations) whose
    ``FORMS_GUARD_SHA256`` states it (``code_problems``, check ihr_refdata.W026 and
    tests.test_forms.GuardChangesShipWithAMigration)."""
    return VERSION


def _migration_modules():
    import importlib
    import pkgutil

    import ihr_refdata.migrations as package

    names = sorted(
        m.name for m in pkgutil.iter_modules(package.__path__) if m.name[:4].isdigit()
    )
    return [
        (name, importlib.import_module(f"ihr_refdata.migrations.{name}"))
        for name in names
    ]


def migration_names():
    """This build's ihr_refdata migrations, oldest first."""
    return [name for name, _module in _migration_modules()]


def code_problems(modules=None):
    """Why this build's guard would not reach existing hospitals (the system check): the
    newest ihr_refdata migration that states ``FORMS_GUARD_SHA256`` must state this build's
    (review Q7: a later migration that does not touch the guard need not restate it).
    ``modules``: [(name, module)], oldest first (tests)."""
    stated = [
        (name, module.FORMS_GUARD_SHA256)
        for name, module in (modules if modules is not None else _migration_modules())
        if hasattr(module, "FORMS_GUARD_SHA256")
    ]
    if not stated:
        return [
            "no ihr_refdata migration states the forms guard: add one (no operations) with "
            f"FORMS_GUARD_SHA256 = {fingerprint()!r}"
        ]
    name, value = stated[-1]
    if value != fingerprint():
        return [
            f"the forms guard differs from the one migration {name} states: add a new "
            f"ihr_refdata migration (no operations) with FORMS_GUARD_SHA256 = "
            f"{fingerprint()!r} so every hospital migrates and gets it"
        ]
    return []


def static_problems(*, installed=True):
    """What is wrong with the guard's wiring in this build (check ihr_refdata.E026)."""
    found = []
    if not installed:
        found.append("the answered-form guard is not installed on post_migrate")
    if set(TRIGGERS) != set(TABLES):
        found.append(
            "the answered-form guard does not cover the form and answer tables"
        )
    if overlap := sorted(set(FROZEN) & set(FREE)):
        found.append(f"columns both frozen and free: {', '.join(overlap)}")
    if set(ANSWER_REVISION) != set(ANSWER_TABLES):
        found.append("an answer table has no entry in ANSWER_REVISION")
    if "slug" not in FROZEN:
        found.append("slug must be frozen (the reserved namespace check fires on it)")
    return found


def _columns(names):
    return sql.SQL(", ").join(sql.Identifier(n) for n in names)


def _row_of(prefix, names):
    return sql.SQL(", ").join(
        sql.SQL("{}.{}").format(sql.SQL(prefix), sql.Identifier(n)) for n in names
    )


def _has_answers(schema):
    """True in SQL when any answer table names the form OLD (soft-deleted answers too)."""
    return sql.SQL("\n           OR ").join(
        sql.SQL("EXISTS (SELECT 1 FROM {} a WHERE a.questionnaire_id = OLD.id)").format(
            sql.Identifier(schema, table)
        )
        for table in ANSWER_TABLES
    )


def _archive_answers(schema):
    """True in SQL when an answer names OLD's live form at OLD's own revision: OLD is the
    archive copy that holds the questions that answer resolves to (CARE 3.1)."""
    return sql.SQL("\n               OR ").join(
        sql.SQL(
            "EXISTS (SELECT 1 FROM {} a WHERE a.questionnaire_id = OLD.latest_revision_id "
            "AND a.{} = OLD.internal_revision)"
        ).format(sql.Identifier(schema, table), sql.Identifier(column))
        for table, column in ANSWER_REVISION.items()
        if column
    )


def function_sql(schema):
    return sql.Composed(
        [
            sql.SQL(_FORM_SQL).format(
                schema=sql.Identifier(schema),
                new_frozen=_row_of("NEW", FROZEN),
                old_frozen=_row_of("OLD", FROZEN),
                has_answers=_has_answers(schema),
                archive_answers=_archive_answers(schema),
            ),
            sql.SQL(_ANSWER_SQL).format(schema=sql.Identifier(schema)),
        ]
    )


def expected_bodies(schema):
    """{function: the body pg_proc.prosrc holds} for this build's guard in ``schema``."""
    bodies = _BODY.findall(function_sql(schema).as_string())
    return dict(zip(FUNCTIONS, bodies, strict=True))


def expected_md5s(cursor, schema):
    """{function: md5} of this build's guard in ``schema``, computed by the database the way
    the restore's package.functions hashes a held one (review G4)."""
    found = {}
    for name, body in expected_bodies(schema).items():
        cursor.execute(
            "SELECT md5(%s || '|' || %s::text[]::text || '|' || false::text)",
            [body, CONFIG],
        )
        found[name] = cursor.fetchone()[0]
    return found


def _definitions_in(cursor, schemas):
    """{schema: {function: (prosrc, proconfig, prosecdef, provolatile)}} as pg_proc holds
    them (the trigger functions take no arguments)."""
    cursor.execute(
        "SELECT n.nspname, p.proname, p.prosrc, p.proconfig, p.prosecdef, p.provolatile "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = ANY(%s) AND p.proname = ANY(%s) AND p.pronargs = 0",
        [list(schemas), list(FUNCTIONS)],
    )
    found = {}
    for schema, name, *rest in cursor.fetchall():
        found.setdefault(schema, {})[name] = tuple(rest)
    return found


def _not_this_builds(schema, definitions):
    """The functions whose body or settings are not this build's (review G3: the comment
    stamp alone can be re-written by whoever edited the body)."""
    bodies = expected_bodies(schema)
    return [
        name
        for name in FUNCTIONS
        if definitions.get(name) != (bodies[name], CONFIG, False, "v")
    ]


def trigger_sql(schema, table, name=None):
    """The CREATE OR REPLACE TRIGGER of one of ours (``name``: the row trigger by default)."""
    for t, trigger, function, tgtype, columns in EXPECTED:
        if t == table and trigger == (name or TRIGGERS[table][0]):
            template = TRUNCATE_SQL if tgtype == TRUNCATE_TYPE else TRIGGER_SQL
            return sql.SQL(template).format(
                name=sql.Identifier(trigger),
                columns=_columns(columns),
                table=sql.Identifier(schema, table),
                function=sql.Identifier(schema, function),
            )
    msg = f"no trigger {name} on {table}"
    raise KeyError(msg)


def _triggers_in(cursor, schemas):
    """{schema: {(table, trigger): (enabled, function, tgtype, sorted columns, WHEN or
    arguments)}} of our triggers on the three tables. ``function`` is its bare name only when
    it lives in the table's own schema, else schema-qualified (review G3: a trigger re-pointed
    to a same-named function elsewhere is not ours)."""
    cursor.execute(
        "SELECT n.nspname, c.relname, t.tgenabled, "
        "CASE WHEN p.pronamespace = c.relnamespace THEN p.proname::text "
        "ELSE p.pronamespace::regnamespace::text || '.' || p.proname END, t.tgtype, "
        "ARRAY(SELECT a.attname::text FROM unnest(t.tgattr::int2[]) k "
        "JOIN pg_attribute a ON a.attrelid = t.tgrelid AND a.attnum = k ORDER BY 1), "
        "t.tgqual IS NOT NULL OR t.tgnargs > 0, t.tgname "
        "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_proc p ON p.oid = t.tgfoid "
        "WHERE n.nspname = ANY(%s) AND c.relname = ANY(%s) AND t.tgname = ANY(%s)",
        [list(schemas), list(TRIGGERS), list(TRIGGER_NAMES)],
    )
    found = {}
    for (
        schema,
        table,
        enabled,
        function,
        tgtype,
        columns,
        extra,
        tgname,
    ) in cursor.fetchall():
        found.setdefault(schema, {})[(table, tgname)] = (
            enabled,
            function,
            tgtype,
            tuple(columns),
            extra,
        )
    return found


def _ready(cursor, schema):
    cursor.execute(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = ANY(%s)",
        [schema, list(TABLES)],
    )
    return cursor.fetchone()[0] == len(TABLES)


def _trigger_problems(schema, here, *, columns_too=True):
    found = []
    for table, trigger, function, tgtype, columns in EXPECTED:
        held = here.get((table, trigger))
        if held is None:
            found.append(f"{schema}.{table}: trigger {trigger} is missing (migrate)")
            continue
        enabled, held_function, held_type, held_columns, extra = held
        if held_function != function:
            found.append(
                f"{schema}.{table}: trigger {trigger} runs {held_function}, not "
                f"{schema}.{function} (migrate)"
            )
            continue
        if enabled not in ("O", "A"):
            found.append(f"{schema}.{table}: trigger {trigger} is disabled (migrate)")
        if held_type != tgtype or extra or (columns_too and held_columns != columns):
            found.append(
                f"{schema}.{table}: trigger {trigger} is not this build's (fires on "
                f"{', '.join(held_columns) or 'every column'}; migrate)"
            )
    return found


def problems(cursor, schema):
    """Why the guard does not hold in ``schema`` (empty when it does, or before its tables).
    The functions are compared with this build's own definition, not only their stamp."""
    if not _ready(cursor, schema):
        return []
    found = []
    functions = db_functions.held(cursor, schema, FUNCTIONS)
    definitions = _definitions_in(cursor, [schema]).get(schema, {})
    if not db_functions.intact(
        functions, FUNCTIONS, FORM_FUNCTION, fingerprint()
    ) or _not_this_builds(schema, definitions):
        found.append(
            f"{schema}.{FORM_FUNCTION}() / {ANSWER_FUNCTION}(): missing or not this build's "
            "(migrate puts them back)"
        )
    found += _trigger_problems(schema, _triggers_in(cursor, [schema]).get(schema, {}))
    return found


def _migrated(cursor, schemas):
    """The schemas whose django_migrations holds the guard's first migration: each must
    hold the guard (review G3: dropping both functions with CASCADE does not un-gate it)."""
    cursor.execute(
        "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = ANY(%s) AND c.relname = 'django_migrations' AND c.relkind = 'r'",
        [list(schemas)],
    )
    have = sorted(name for (name,) in cursor.fetchall())
    if not have:
        return set()
    cursor.execute(
        sql.SQL(" UNION ALL ").join(
            sql.SQL("SELECT {} FROM {} WHERE app = 'ihr_refdata' AND name = {}").format(
                sql.Literal(schema),
                sql.Identifier(schema, "django_migrations"),
                sql.Literal(FIRST_MIGRATION),
            )
            for schema in have
        )
    )
    return {name for (name,) in cursor.fetchall()}


def runtime_problems_by_schema(cursor, schemas):
    """What an app process gates a hospital for (db_roles' "controls"), as
    placement.runtime_problems_by_schema: a function missing or changed since it was
    installed, a function stamped as this build's whose body or settings are not this build's,
    a trigger missing, disabled, pointing elsewhere (another schema's function of the same name
    too) or not of this build's type, and, when the installed guard is this build's, a trigger
    whose columns are not this build's. An older build's guard, unchanged as installed, is not
    a gate (a guard change comes with an ihr_refdata migration, so that hospital is behind and
    not served anyway). A schema with neither function nor trigger is skipped only while its
    django_migrations lacks the guard's first migration (no guard yet); one that has it and
    lost both is gated (review G3). Known limit: an owner who re-stamps an edited body with an
    older build's version passes here; ``problems()`` (the fleet check) names it and migrate
    puts it back."""
    schemas = list(schemas)
    held = db_functions.held_in(cursor, schemas, FUNCTIONS)
    triggers = _triggers_in(cursor, schemas)
    definitions = _definitions_in(cursor, schemas)
    bare = [s for s in schemas if s not in held and s not in triggers]
    required = _migrated(cursor, bare) if bare else set()
    found = {}
    for schema in schemas:
        if schema not in held and schema not in triggers and schema not in required:
            continue
        functions = held.get(schema, {})
        here = []
        if not set(FUNCTIONS) <= set(functions):
            here.append(
                f"{schema}.{FORM_FUNCTION}() / {ANSWER_FUNCTION}(): missing (migrate puts "
                "them back)"
            )
        elif not db_functions.unchanged(functions, FUNCTIONS, FORM_FUNCTION):
            here.append(
                f"{schema}.{FORM_FUNCTION}() / {ANSWER_FUNCTION}(): changed since they were "
                "installed (migrate puts them back)"
            )
        this_build = set(FUNCTIONS) <= set(functions) and db_functions.intact(
            functions, FUNCTIONS, FORM_FUNCTION, fingerprint()
        )
        if this_build and _not_this_builds(schema, definitions.get(schema, {})):
            here.append(
                f"{schema}.{FORM_FUNCTION}() / {ANSWER_FUNCTION}(): stamped as this build's "
                "but not its definition (migrate puts them back)"
            )
        here += _trigger_problems(
            schema, triggers.get(schema, {}), columns_too=this_build
        )
        if here:
            found[schema] = here
    return found


def ensure(connection, schema):
    """Install or repair the guard in a hospital schema (post_migrate, as the owner). True if
    it changed something."""
    with connection.cursor() as cursor:
        if not _ready(cursor, schema) or not problems(cursor, schema):
            return False
    with transaction.atomic(using=connection.alias), connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute(function_sql(schema))
        for table, trigger, *_rest in EXPECTED:
            # Every time (CREATE OR REPLACE): an earlier build's, or one re-created by hand,
            # takes this build's definition
            cursor.execute(trigger_sql(schema, table, trigger))
            cursor.execute(
                sql.SQL("ALTER TABLE {} ENABLE TRIGGER {}").format(
                    sql.Identifier(schema, table), sql.Identifier(trigger)
                )
            )
        db_functions.write_stamp(
            cursor, schema, FORM_FUNCTION, "", list(FUNCTIONS), fingerprint()
        )
    return True
