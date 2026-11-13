"""Database functions whose code is ours, kept current by post_migrate (P1-T19a review C5, S1).

A migration installs such a function once, from SQL frozen in the migration file. After that
the code is the authority: ``post_migrate`` calls an ``ensure`` that compares what the database
holds with this build's SQL and replaces it when they differ, so any migrate repairs a function
that was dropped, altered (``SECURITY INVOKER``, a lost ``search_path``) or edited by hand, and
upgrades every schema when the code changes.

How it knows: a COMMENT on one function of the set (the anchor) holds this build's version (a
hash of its SQL) and a fingerprint of each function as the database holds it (body, settings,
security). ``CREATE OR REPLACE`` keeps a comment, so a body or attribute changed afterwards no
longer matches its fingerprint, and a newer build's version does not match the comment.
"""

import hashlib

from psycopg import sql


def version(*texts):
    return hashlib.sha256("\0".join(texts).encode()).hexdigest()


def held(cursor, schema, names):
    """{name: (oid, comment, fingerprint)} of the functions ``names`` in ``schema``."""
    return held_in(cursor, [schema], names).get(schema, {})


def held_in(cursor, schemas, names):
    """``held`` for many schemas in one query: {schema: {name: (oid, comment, fingerprint)}}
    (the run-time check reads every hospital's at once)."""
    cursor.execute(
        "SELECT n.nspname, p.proname, p.oid, obj_description(p.oid, 'pg_proc'), "
        "md5(p.prosrc || '|' || coalesce(p.proconfig::text, '') || '|' || "
        "p.prosecdef::text || '|' || p.provolatile::text) "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = ANY(%s) AND p.proname = ANY(%s)",
        [list(schemas), list(names)],
    )
    found = {}
    for schema, name, *rest in cursor.fetchall():
        found.setdefault(schema, {})[name] = tuple(rest)
    return found


def stamp(current_version, functions, names):
    return " ".join([current_version, *(functions[name][2] for name in names)])


def intact(functions, names, anchor, current_version):
    """Every function is there and the anchor's comment matches them and this build."""
    return set(names) <= set(functions) and functions[anchor][1] == stamp(
        current_version, functions, names
    )


def unchanged(functions, names, anchor):
    """Every function is there and as it was installed, whatever build installed it: the
    anchor's stamp names their fingerprints (an older build's set is unchanged too)."""
    if not set(names) <= set(functions):
        return False
    comment = functions[anchor][1] or ""
    return comment.split(" ")[1:] == [functions[name][2] for name in names]


def write_stamp(cursor, schema, anchor, arguments, names, current_version):
    """After (re)installing: the comment that marks the set as this build's."""
    functions = held(cursor, schema, names)
    cursor.execute(
        sql.SQL("COMMENT ON FUNCTION {}.{}({}) IS {}").format(
            sql.Identifier(schema),
            sql.Identifier(anchor),
            sql.SQL(arguments),
            sql.Literal(stamp(current_version, functions, names)),
        )
    )
