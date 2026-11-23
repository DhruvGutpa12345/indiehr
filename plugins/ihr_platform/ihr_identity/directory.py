"""The staff directory behind the hospital switcher (P1-T31; docs/reviews/p1-t31/design.md §3.2,
as changed by attack.md "Proposed orchestrator decision"; Q-K144 A, Q-K145 A).

``public.ihr_platform_staffdirectoryentry`` (ihr_platform.models.StaffDirectoryEntry) holds one
row per *listed* link: a Keycloak person (``keycloak_sub``) and a hospital. A link is listed when,
in that hospital's schema, a ``StaffIdentity`` with that sub points to a ``users_user`` row that
is active, not deleted, not a superuser and not a service account (``LISTED``). Superusers are
always left out, whatever ``IHR_STAFF_REFUSE_SUPERUSERS`` says (the production rule; a trigger
cannot read settings).

Who writes it: in each hospital schema, the trigger function ``ihr_directory_touch()``
(SECURITY DEFINER, owned by the database owner, ``search_path`` pinned, EXECUTE from nobody:
db_roles.FUNCTIONS). For every person a write touches it recomputes the link from that
schema's own rows and inserts or deletes the one (sub, hospital) row. The hospital is the one
whose ``schema_name`` is the schema that fired the trigger (``TG_TABLE_SCHEMA``), never a row's
value, so a write in one hospital's schema only ever writes rows for that hospital (a role that
can write ``StaffIdentity.keycloak_sub`` there can still put its own hospital in someone's menu;
the click then ends at Keycloak's per-hospital gate: attack D9). A schema no hospital names
(a restore's parked or staging schema) writes nothing. Triggers, not signals: our own code
activates and deactivates people with ``queryset.update()`` (views._finish_setup, join.py,
invitations.py), raw SQL and CARE's bulk paths write these tables too, and the app role never
gets a write right on the table.

- ``ihr_directory_link``: AFTER INSERT, DELETE, UPDATE OF user_id, keycloak_sub on
  ``ihr_identity_staffidentity`` (a user's hard delete reaches it through the cascade);
- ``ihr_directory_user``: AFTER UPDATE OF the four ``LISTED`` columns on ``users_user``, with a
  WHEN clause, so CARE's ``cached_role_orgs`` re-saves never start the function;
- ``ihr_directory_truncate``: AFTER TRUNCATE on ``ihr_identity_staffidentity``: every row of
  that hospital goes.

Two recomputes of one link (two transactions) are serialised by
``pg_advisory_xact_lock(hospital id, hashtext(sub))`` (attack S9), taken before the link is read,
people in sub order; the repair (``rebuild``, P1-T31b) takes the same locks.

Repair (P1-T31b): ``rebuild(hospital)`` makes one hospital's rows equal to its listed links,
under the same per-person locks; ``manage.py ihr_staff_directory --check|--repair`` runs it
for every hospital (attack D2), and a single-hospital restore runs it in its reconcile step and
in ``abort()`` (attack D6). Each finding writes ``log_line(slug, "drift")``; a restore's failed
rebuild ``log_line(slug, "failed")`` (deploy/logs/alerts/directory.yml).

Installed and first filled by ihr_identity 0004 (its SQL frozen there), then kept current by
``ensure_directory`` on every hospital schema's post_migrate, stamped like the sealing
functions (ihr_platform/db_functions.py). This module's SQL is canonical: a change to it comes
with a new ihr_identity migration stating ``fingerprint()`` as ``DIRECTORY_SHA256`` (check
ihr_identity.W027), so every hospital migrates and gets it. Drift in one schema is never a
serving gate (attack D1): ``problems()`` reports it for ``ihr_staff_directory --check``
(P1-T31b), and the next migrate puts it back. Check ihr_identity.E027 is static: the install
is wired to post_migrate, and CARE still has the columns the triggers name.

The switcher's endpoint (P1-T31c): ``GET /api/ihr/my-hospitals/`` (``MyHospitalsView``, staff
session cookie only) answers from ``hospitals_for``, the one reader of the table: the signed-in
person's own rows, never a parameter (Q-K147; ihr_identity.tests.test_my_hospitals).
"""

import re

from django.core.exceptions import ObjectDoesNotExist
from django.db import connection as db_connection
from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from ihr_platform import db_functions, links, release
from psycopg import sql
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ihr_identity.authentication import StaffSessionAuthentication
from ihr_identity.staff_access import NotCrossSite

FUNCTION = "ihr_directory_touch"
LINK_TABLE = "ihr_identity_staffidentity"
USER_TABLE = "users_user"
TABLE = "ihr_platform_staffdirectoryentry"  # in public
# The users_user columns that decide a listed link (each a boolean)
LISTED = ("is_active", "deleted", "is_superuser", "is_service_account")
LINK_COLUMNS = ("user_id", "keycloak_sub")
# The newest ihr_identity migration states this build's fingerprint (W027)
FIRST_MIGRATION = "0004_staff_directory"
# The fixed line a log alert reads (deploy/logs/alerts/directory.yml): a hospital's rows or
# triggers differ from its links (--check), or a restore could not rebuild its rows
OUTCOMES = ("drift", "failed")
LOCK_ROUNDS = (
    5  # rebuild: people linked while it waited for their locks are locked next round
)

FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION {schema}.ihr_directory_touch() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    hid bigint;
    subs uuid[];
    one uuid;
    listed boolean;
BEGIN
    -- The hospital is the schema that fired the trigger, never a value of the row
    SELECT h.id INTO hid FROM public.ihr_platform_hospital h
        WHERE h.schema_name = TG_TABLE_SCHEMA AND h.schema_name <> 'public';
    IF hid IS NULL THEN
        RETURN NULL;  -- a restore's parked or staging schema: no hospital's
    END IF;
    IF TG_OP = 'TRUNCATE' THEN
        DELETE FROM public.ihr_platform_staffdirectoryentry d WHERE d.hospital_id = hid;
        RETURN NULL;
    END IF;
    IF TG_TABLE_NAME = 'ihr_identity_staffidentity' THEN
        IF TG_OP <> 'INSERT' THEN
            subs := array_append(subs, OLD.keycloak_sub);
        END IF;
        IF TG_OP <> 'DELETE' THEN
            subs := array_append(subs, NEW.keycloak_sub);
        END IF;
    ELSE
        EXECUTE format('SELECT array_agg(s.keycloak_sub) FROM %I.ihr_identity_staffidentity s '
                       'WHERE s.user_id IN ($1, $2)', TG_TABLE_SCHEMA)
            INTO subs USING OLD.id, NEW.id;
    END IF;
    FOR one IN SELECT DISTINCT x FROM unnest(subs) AS x WHERE x IS NOT NULL ORDER BY 1 LOOP
        PERFORM pg_advisory_xact_lock(hid::integer, hashtext(one::text));
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM %1$I.ihr_identity_staffidentity s '
                       'JOIN %1$I.users_user u ON u.id = s.user_id WHERE s.keycloak_sub = $1 '
                       'AND u.is_active AND NOT u.deleted AND NOT u.is_superuser '
                       'AND NOT u.is_service_account)', TG_TABLE_SCHEMA)
            INTO listed USING one;
        IF listed THEN
            INSERT INTO public.ihr_platform_staffdirectoryentry (keycloak_sub, hospital_id)
                VALUES (one, hid) ON CONFLICT (keycloak_sub, hospital_id) DO NOTHING;
        ELSE
            DELETE FROM public.ihr_platform_staffdirectoryentry d
                WHERE d.keycloak_sub = one AND d.hospital_id = hid;
        END IF;
    END LOOP;
    RETURN NULL;
END $f$;
REVOKE ALL ON FUNCTION {schema}.ihr_directory_touch() FROM PUBLIC;
"""
LINK_SQL = (
    "CREATE OR REPLACE TRIGGER ihr_directory_link AFTER INSERT OR DELETE OR UPDATE OF "
    "user_id, keycloak_sub ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()"
)
USER_SQL = (
    "CREATE OR REPLACE TRIGGER ihr_directory_user AFTER UPDATE OF is_active, deleted, "
    "is_superuser, is_service_account ON {table} FOR EACH ROW WHEN ("
    "OLD.is_active IS DISTINCT FROM NEW.is_active OR OLD.deleted IS DISTINCT FROM NEW.deleted "
    "OR OLD.is_superuser IS DISTINCT FROM NEW.is_superuser "
    "OR OLD.is_service_account IS DISTINCT FROM NEW.is_service_account) "
    "EXECUTE FUNCTION {function}()"
)
TRUNCATE_SQL = (
    "CREATE OR REPLACE TRIGGER ihr_directory_truncate AFTER TRUNCATE ON {table} "
    "FOR EACH STATEMENT EXECUTE FUNCTION {function}()"
)
# pg_trigger.tgtype bits: ROW 1 | BEFORE 2 | INSERT 4 | DELETE 8 | UPDATE 16 | TRUNCATE 32
# {trigger: (table, SQL, tgtype, sorted UPDATE OF columns, has a WHEN clause)}
TRIGGERS = {
    "ihr_directory_link": (
        LINK_TABLE,
        LINK_SQL,
        1 | 4 | 8 | 16,
        tuple(sorted(LINK_COLUMNS)),
        False,
    ),
    "ihr_directory_user": (USER_TABLE, USER_SQL, 1 | 16, tuple(sorted(LISTED)), True),
    "ihr_directory_truncate": (LINK_TABLE, TRUNCATE_SQL, 32, (), False),
}
VERSION = db_functions.version(
    FUNCTION_SQL, LINK_SQL, USER_SQL, TRUNCATE_SQL, repr(sorted(TRIGGERS.items()))
)
# What pg_proc holds besides the body: the pinned search_path, SECURITY DEFINER, volatile
CONFIG = ["search_path=pg_catalog, pg_temp"]
_BODY = re.compile(r"AS \$f\$(.*?)\$f\$;", re.DOTALL)


def fingerprint():
    """What the directory's function and triggers are in this build (W027 compares it with
    the newest ihr_identity migration's ``DIRECTORY_SHA256``)."""
    return VERSION


def function_sql(schema):
    return sql.SQL(FUNCTION_SQL).format(schema=sql.Identifier(schema))


def expected_body():
    """The body pg_proc.prosrc holds (the same in every schema: it names none)."""
    return _BODY.search(FUNCTION_SQL).group(1)


def trigger_sql(schema, name):
    table, template, *_rest = TRIGGERS[name]
    return sql.SQL(template).format(
        table=sql.Identifier(schema, table),
        function=sql.Identifier(schema, FUNCTION),
    )


# --- static checks (E027, W027) ----------------------------------------------------------------


def _migration_modules():
    import importlib
    import pkgutil

    import ihr_identity.migrations as package

    names = sorted(
        m.name for m in pkgutil.iter_modules(package.__path__) if m.name[:4].isdigit()
    )
    return [
        (name, importlib.import_module(f"ihr_identity.migrations.{name}"))
        for name in names
    ]


def code_problems(modules=None):
    """Why this build's directory SQL would not reach existing hospitals (W027): the newest
    ihr_identity migration that states ``DIRECTORY_SHA256`` must state this build's.
    ``modules``: [(name, module)], oldest first (tests)."""
    stated = [
        (name, module.DIRECTORY_SHA256)
        for name, module in (modules if modules is not None else _migration_modules())
        if hasattr(module, "DIRECTORY_SHA256")
    ]
    if not stated:
        return [
            "no ihr_identity migration states the staff directory's SQL: add one (no "
            f"operations) with DIRECTORY_SHA256 = {fingerprint()!r}"
        ]
    name, value = stated[-1]
    if value != fingerprint():
        return [
            f"the staff directory's SQL differs from the one migration {name} states: add a "
            f"new ihr_identity migration (no operations) with DIRECTORY_SHA256 = "
            f"{fingerprint()!r} so every hospital migrates and gets it"
        ]
    return []


def _user_columns():
    from django.contrib.auth import get_user_model

    return {
        field.column
        for field in get_user_model()._meta.concrete_fields  # noqa: SLF001
    }


def static_problems(*, installed=True, user_columns=None):
    """What is wrong with the directory's wiring in this build (E027): its install on
    post_migrate, and the user columns its trigger and rule name (an upstream rename would
    otherwise end the trigger at the next migrate, or list nobody)."""
    found = []
    if not installed:
        found.append(
            "the staff directory's trigger is not installed on post_migrate "
            "(ihr_identity.directory.ensure_directory)"
        )
    columns = _user_columns() if user_columns is None else set(user_columns)
    if missing := sorted(set(LISTED) - columns):
        found.append(
            f"CARE's user model has no column {', '.join(missing)}: the staff directory's "
            "trigger and listing rule name it (ihr_identity/directory.py LISTED)"
        )
    return found


# --- what a schema holds -----------------------------------------------------------------------


def _ready(cursor, schema):
    cursor.execute(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = ANY(%s) AND c.relkind = 'r'",
        [schema, [LINK_TABLE, USER_TABLE]],
    )
    return cursor.fetchone()[0] == 2  # noqa: PLR2004 (the two tables)


def _definition(cursor, schema):
    """(prosrc, proconfig, prosecdef, provolatile) of the function, or None."""
    cursor.execute(
        "SELECT p.prosrc, p.proconfig, p.prosecdef, p.provolatile FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = %s AND p.proname = %s AND p.pronargs = 0",
        [schema, FUNCTION],
    )
    row = cursor.fetchone()
    return tuple(row) if row else None


def _triggers(cursor, schema):
    """{trigger: (table, enabled, function, tgtype, sorted columns, has WHEN, has args)} of
    ours in ``schema``. ``function`` is bare only when it lives in the table's own schema."""
    cursor.execute(
        "SELECT t.tgname, c.relname, t.tgenabled, "
        "CASE WHEN p.pronamespace = c.relnamespace THEN p.proname::text "
        "ELSE p.pronamespace::regnamespace::text || '.' || p.proname END, t.tgtype, "
        "ARRAY(SELECT a.attname::text FROM unnest(t.tgattr::int2[]) k "
        "JOIN pg_attribute a ON a.attrelid = t.tgrelid AND a.attnum = k ORDER BY 1), "
        "t.tgqual IS NOT NULL, t.tgnargs > 0 "
        "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_proc p ON p.oid = t.tgfoid "
        "WHERE n.nspname = %s AND t.tgname = ANY(%s)",
        [schema, list(TRIGGERS)],
    )
    return {
        name: (table, enabled, function, tgtype, tuple(columns), qual, args)
        for name, table, enabled, function, tgtype, columns, qual, args in cursor.fetchall()
    }


def problems(cursor, schema):
    """Why the directory's function or triggers in ``schema`` are not this build's (empty
    when they are, or before its tables exist). The function is compared with this build's
    own definition, not only its stamp. Reported only (``ihr_staff_directory --check``): drift
    never gates a hospital (attack D1); the next migrate puts it back."""
    if not _ready(cursor, schema):
        return []
    found = []
    functions = db_functions.held(cursor, schema, [FUNCTION])
    if not db_functions.intact(functions, [FUNCTION], FUNCTION, fingerprint()) or (
        _definition(cursor, schema) != (expected_body(), CONFIG, True, "v")
    ):
        found.append(
            f"{schema}.{FUNCTION}(): missing or not this build's (migrate puts it back)"
        )
    held = _triggers(cursor, schema)
    for name, (table, _sql, tgtype, columns, qual) in TRIGGERS.items():
        here = held.get(name)
        if here is None:
            found.append(f"{schema}.{table}: trigger {name} is missing (migrate)")
            continue
        on, enabled, function, held_type, held_columns, held_qual, args = here
        if on != table or function != FUNCTION:
            found.append(
                f"{schema}.{on}: trigger {name} runs {function}, not {schema}.{FUNCTION} "
                f"on {table} (migrate)"
            )
            continue
        if enabled not in ("O", "A"):
            found.append(f"{schema}.{table}: trigger {name} is disabled (migrate)")
        if (held_type, held_columns, held_qual, args) != (tgtype, columns, qual, False):
            found.append(
                f"{schema}.{table}: trigger {name} is not this build's (migrate)"
            )
    return found


# --- the rows (repair) -------------------------------------------------------------------------


def log_line(slug, outcome):
    """``ihr-directory: hospital=<slug> outcome=<drift|failed>``: never a person's sub."""
    if outcome not in OUTCOMES:
        msg = f"not a directory outcome: {outcome!r}"
        raise ValueError(msg)
    return f"ihr-directory: hospital={slug} outcome={outcome}"


def listed_subs(cursor, schema):
    """The people (Keycloak subs) this schema lists: a link to an active, not deleted user who
    is neither a superuser nor a service account (the trigger's rule). None for ``public``;
    an empty set before the schema has its tables."""
    if schema == "public":
        return None
    if not _ready(cursor, schema):
        return set()
    cursor.execute(
        sql.SQL(
            "SELECT DISTINCT s.keycloak_sub FROM {} s JOIN {} u ON u.id = s.user_id "
            "WHERE u.is_active AND NOT u.deleted AND NOT u.is_superuser "
            "AND NOT u.is_service_account"
        ).format(sql.Identifier(schema, LINK_TABLE), sql.Identifier(schema, USER_TABLE))
    )
    return {row[0] for row in cursor.fetchall()}


def row_subs(cursor, hospital_id):
    cursor.execute(
        f"SELECT keycloak_sub FROM public.{TABLE} WHERE hospital_id = %s",  # noqa: S608
        [hospital_id],
    )
    return {row[0] for row in cursor.fetchall()}


def differences(cursor, hospital):
    """(missing, extra): the subs whose row this hospital lacks, and the rows it should not
    have (read-only, no locks). For ``public`` every row is extra."""
    want = listed_subs(cursor, hospital.schema_name)
    have = row_subs(cursor, hospital.pk)
    if want is None:
        return set(), have
    return want - have, have - want


def _lock(cursor, hospital_id, subs):
    """The trigger's per-person locks (attack S9), in its order (by sub)."""
    if subs:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s::integer, hashtext(x::text)) "
            "FROM (SELECT DISTINCT x FROM unnest(%s::uuid[]) AS x ORDER BY 1) s",
            [hospital_id, sorted(subs)],
        )
        cursor.fetchall()


def rebuild(hospital):
    """Make ``hospital``'s rows equal to the links its schema lists, in one transaction, under
    the trigger's per-person locks (attack S9): a set difference of (sub, hospital) pairs.
    Owner only (the app reads the table). Writes nothing for ``public`` (S8). Returns
    ``{"added": n, "removed": n}``."""
    from django.db import connection

    counts = {"added": 0, "removed": 0}
    if hospital.schema_name == "public":
        return counts
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '10s'")
        locked = set()
        for _round in range(LOCK_ROUNDS):
            want = listed_subs(cursor, hospital.schema_name)
            have = row_subs(cursor, hospital.pk)
            need = (want | have) - locked
            if not need:
                break
            _lock(cursor, hospital.pk, need)
            locked |= need
        else:
            msg = f"{hospital.slug}: its links kept changing while the rebuild waited: run it again"
            raise RuntimeError(msg)
        if extra := sorted(have - want):
            cursor.execute(
                f"DELETE FROM public.{TABLE} WHERE hospital_id = %s "  # noqa: S608
                "AND keycloak_sub = ANY(%s::uuid[])",
                [hospital.pk, extra],
            )
            counts["removed"] = cursor.rowcount
        if missing := sorted(want - have):
            cursor.execute(
                f"INSERT INTO public.{TABLE} (keycloak_sub, hospital_id) "  # noqa: S608
                "SELECT x, %s FROM unnest(%s::uuid[]) AS x "
                "ON CONFLICT (keycloak_sub, hospital_id) DO NOTHING",
                [hospital.pk, missing],
            )
            counts["added"] = cursor.rowcount
    return counts


def clear(hospital):
    """Delete every row of a hospital the switcher never lists (not migrated, ``public``):
    the repair's replacement for a fleet step (attack D2). Owner only. Returns the count."""
    from django.db import connection

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM public.{TABLE} WHERE hospital_id = %s",  # noqa: S608
            [hospital.pk],
        )
        return cursor.rowcount


def ensure_directory(connection, schema):
    """Install or repair the directory's function and triggers in a hospital schema
    (post_migrate, as the owner, before the grants). Changes nothing when they are current.
    True if it changed something. Never writes a directory row (a restore's migrate can be
    aborted: attack D6); the first fill is ihr_identity 0004's, the repair P1-T31b's."""
    with connection.cursor() as cursor:
        if not _ready(cursor, schema) or not problems(cursor, schema):
            return False
    with transaction.atomic(using=connection.alias), connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute(function_sql(schema))
        for name, (table, *_rest) in TRIGGERS.items():
            # Every time (CREATE OR REPLACE): an earlier build's, or one re-made by hand,
            # takes this build's definition
            cursor.execute(trigger_sql(schema, name))
            cursor.execute(
                sql.SQL("ALTER TABLE {} ENABLE TRIGGER {}").format(
                    sql.Identifier(schema, table), sql.Identifier(name)
                )
            )
        db_functions.write_stamp(
            cursor, schema, FUNCTION, "", [FUNCTION], fingerprint()
        )
    return True


# --- the switcher's endpoint (P1-T31c) ----------------------------------------------------------

# Where each hospital's own sign-in starts (views.login): no ``?next``, so it lands on "/" (D10)
LOGIN_PATH = "/api/ihr/auth/login/"


def hospitals_for(request):
    """The hospitals the signed-in person may switch to, for GET /api/ihr/my-hospitals/
    (Q-K146 A, Q-K147): the one function that reads the directory. Only the rows of the
    request's own person (``request.user.ihr_identity``), never a parameter. Listed: ACTIVE
    hospitals with a Keycloak organisation and a primary address, never ``public`` (S8); the
    current hospital always, row or not. Each is ``{name, url, current, available}``: the url
    is that hospital's own sign-in (``links.origin_for``), ``available`` is ``servable()``
    (a held or other-build hospital is listed, disabled). Current first, then by name (case
    ignored). Two queries whatever the number of hospitals (D3): the hospitals with their
    first primary address, and their states (``release.servable_many``)."""
    from ihr_platform.models import Domain, Hospital, StaffDirectoryEntry

    current = db_connection.tenant
    sub = request.user.ihr_identity.keycloak_sub
    primary = (
        Domain.objects.filter(tenant=OuterRef("pk"), is_primary=True)
        .order_by("id")
        .values("domain")[:1]
    )
    listed = StaffDirectoryEntry.objects.filter(keycloak_sub=sub).values("hospital")
    hospitals = list(
        Hospital.objects.filter(Q(pk__in=listed) | Q(pk=current.pk))
        .filter(status=Hospital.Status.ACTIVE)
        .filter(keycloak_org_id__isnull=False)
        .exclude(schema_name="public")
        .annotate(address=Subquery(primary))
        .filter(address__isnull=False)
        .only("pk", "name", "schema_name", "status")
    )
    available = release.servable_many(hospitals)
    hospitals.sort(key=lambda h: (h.pk != current.pk, h.name.casefold(), h.pk))
    return [
        {
            "name": hospital.name,
            "url": links.origin_for(hospital.address) + LOGIN_PATH,
            "current": hospital.pk == current.pk,
            "available": available[hospital.pk],
        }
        for hospital in hospitals
    ]


class NotServiceAccount(BasePermission):
    """A machine account has no hospitals to switch to (attack S6)."""

    message = "Only staff can do this."

    def has_permission(self, request, view):
        return not getattr(request.user, "is_service_account", False)


@method_decorator(never_cache, name="dispatch")
class MyHospitalsView(APIView):
    """GET /api/ihr/my-hospitals/: the hospital switcher's list (design §3.4, Q-K147).

    Staff only, by the staff session cookie alone: never a service-account token, a CARE JWT
    or a patient's OTP token (S6), and 403 for every refusal (D14; no 401: the staff class has
    no WWW-Authenticate). ``NotCrossSite`` first, so a cross-site page learns nothing (Z7).
    Reads no parameters (D13), caches nothing (Q-K149), writes no audit entry: no patient data
    and no change (the switch itself is a sign-in at the target, recorded there)."""

    # Staff only (S6): this class alone (the defaults add DRF machine tokens), and never a
    # service account
    authentication_classes = [StaffSessionAuthentication]
    permission_classes = (NotCrossSite, IsAuthenticated, NotServiceAccount)
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        try:
            request.user.ihr_identity  # noqa: B018 (a person with no link has no list)
        except ObjectDoesNotExist as exc:
            raise PermissionDenied(NotServiceAccount.message) from exc
        return Response({"hospitals": hospitals_for(request)})
