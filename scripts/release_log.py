#!/usr/bin/env python3
"""The release log of a staging server (P1-T18b; docs/reviews/p1-t18/design.md items 13-16).

``deploy/staging/releases/releases.log`` (on a server /srv/indiehr/deploy/staging/releases/) has
one line per release that finished (its smoke passed) and per rollback, written only by
scripts/staging.sh. A line is ``key=value`` words separated by single spaces:

    kind=release tag=<release tag> time=<ISO 8601> backend=sha256:<id> frontend=sha256:<id>
      postgres=sha256:<id> keycloak=sha256:<id> [acmedns=... grafana=...]
      tenant_fp=<sha256> public_fp=<sha256> restore_point=<release-<tag>-<epoch>|-> lsn=<lsn|->
      rp_time=<epoch|-> [rp_from=<tag>] audit=<ok|problems|timeout|skipped|-> [seal=... seal_s=N]
      [via=release|plain] [migrations=none|started] [finished=<ISO 8601>]
    kind=rollback tag=<the release rolled back to> from=<the release left> restored=<restore point|no>
      time=...

Values are image ids, fingerprints, names and times only: never a setting or a secret (a value
may hold only letters, digits and ``: . _ + -``; a WAL position ``lsn`` also one ``/``).
``releases/attempt`` holds the one release in progress (the same fields, ``kind=attempt``),
written by ``staging.sh release`` before its first change (``migrations=none`` until its restore
point is taken, then ``started``) and by a plain start before ``up``; removed when it is logged
or rolled back: a release whose smoke never passed is never a rollback target, but the restore
point it took is still found. The restore point belongs to the open window, not to a build: a
later ``release`` of another build while an attempt is open carries the point over
(``rp_from`` names the release it was taken for), and a plain start refuses then.

A restore point's name is unique (``release-<tag>-<epoch>``): Postgres stops at the first
point of a name, so a name used twice could stop a restore at an older point.

Release tag: the commit's first 12 hex digits, or for a build of a tree with uncommitted changes
``<sha12>-dirty-<first 8 hex digits of the backend image id>`` (two dirty builds of one commit
must not share a tag).

The log is a stack: a release pushes its tag, a rollback pops back to the release it names.
``rollback`` leaves the top for the release below it. Lines of one tag on top of each other (a
build deployed again, logged twice before P1-T18b's review) count as one release, whose restore
point is the oldest one of them (the window's). An open attempt is what a rollback leaves, for
the release logged last, whether the attempt's build already runs or it stopped before its start.

Usage (stdlib only; the server has python3 but no checkout):
    release_log.py tag <git sha> <yes|no dirty> <backend image id>
    release_log.py check-line <key=value>...           exit 1 with the reason if a value is bad
    release_log.py line <key=value>...                 the checked line, as written
    release_log.py state <log> [<attempt file>]        the stack, newest last (one tag a line)
    release_log.py plan <log> <attempt file> <running tag> [--restore]
                                                      key=value lines for staging.sh rollback
    release_log.py keep <log> <attempt file> <n> [<running tag>]
                                                      the release tags whose images stay
    release_log.py needs-release <log> <attempt file|-> <tenant fp> <public fp> <db-created 0|1>
                                                      exit 0 if a plain start is enough, 3 if not
    release_log.py point-free <log> <attempt file> <restore point name>
                                                      exit 1 if a line already names that point
"""

import re
import sys
from datetime import datetime

VALUE_RE = re.compile(r"^[A-Za-z0-9:._+-]{1,128}$")
KEY_RE = re.compile(r"^[a-z_]{1,32}$")
TAG_RE = re.compile(r"^[0-9a-f]{12}(-dirty-[0-9a-f]{8})?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FP_RE = re.compile(r"^[0-9a-f]{64}$")
LSN_RE = re.compile(r"^([0-9A-F]{1,8}/[0-9A-F]{1,8}|-)$")
# release-<tag>-<epoch> (the epoch: unique per window; names without it are from before the review)
POINT_RE = re.compile(r"^release-[0-9a-f]{12}(-dirty-[0-9a-f]{8})?(-[0-9]{10})?$")
CHOICES = {"via": ("release", "plain"), "migrations": ("none", "started")}
KINDS = ("release", "rollback", "attempt")
IMAGES = ("backend", "frontend", "postgres", "keycloak", "acmedns", "grafana")
REQUIRED = {
    "release": ("tag", "time", "backend", "frontend", "postgres", "keycloak", "tenant_fp", "public_fp"),
    "attempt": ("tag", "time", "backend", "frontend", "postgres", "keycloak", "tenant_fp", "public_fp"),
    "rollback": ("tag", "time", "from", "restored"),
}


class LogError(Exception):
    pass


def release_tag(sha, dirty, backend_id):
    if not SHA_RE.match(sha):
        raise LogError(f"not a git commit: {sha!r}")
    if dirty not in ("yes", "no"):
        raise LogError("dirty is yes or no")
    if not ID_RE.match(backend_id):
        raise LogError(f"not an image id: {backend_id!r}")
    tag = sha[:12]
    return tag if dirty == "no" else f"{tag}-dirty-{backend_id[7:15]}"


def check(fields):
    """Raise LogError unless ``fields`` (a dict) make a valid line."""
    kind = fields.get("kind")
    if kind not in KINDS:
        raise LogError(f"kind is one of {', '.join(KINDS)}")
    for key, value in fields.items():
        if not KEY_RE.match(key):
            raise LogError(f"bad key {key!r}")
        if not (LSN_RE if key == "lsn" else VALUE_RE).match(value):
            raise LogError(f"bad value for {key}")
    if missing := [k for k in REQUIRED[kind] if k not in fields]:
        raise LogError(f"{kind} line without {', '.join(missing)}")
    for key in ("tag", "from", "rp_from"):
        if key in fields and not TAG_RE.match(fields[key]):
            raise LogError(f"{key} is not a release tag")
    if fields.get("restore_point", "-") != "-" and not POINT_RE.match(fields["restore_point"]):
        raise LogError("restore_point is not a release's restore point")
    if fields.get("restored", "no") != "no" and not POINT_RE.match(fields["restored"]):
        raise LogError("restored is not a release's restore point")
    for key, allowed in CHOICES.items():
        if key in fields and fields[key] not in allowed:
            raise LogError(f"{key} is one of {', '.join(allowed)}")
    for key in IMAGES:
        if key in fields and not ID_RE.match(fields[key]):
            raise LogError(f"{key} is not an image id")
    for key in ("tenant_fp", "public_fp"):
        if key in fields and not FP_RE.match(fields[key]):
            raise LogError(f"{key} is not a fingerprint")
    return fields


def parse_words(words):
    fields = {}
    for word in words:
        key, sep, value = word.partition("=")
        if not sep:
            raise LogError(f"not key=value: {word!r}")
        if key in fields:
            raise LogError(f"{key} twice")
        fields[key] = value
    return check(fields)


def format_line(fields):
    order = ["kind", "tag", "from", "time"]
    keys = order + sorted(k for k in fields if k not in order)
    return " ".join(f"{k}={fields[k]}" for k in keys if k in fields)


def read_log(path):
    """The log's entries, oldest first. A line that does not parse stops everything: a log
    that was edited by hand must be fixed, never guessed around."""
    try:
        with open(path, encoding="ascii") as f:
            text = f.read()
    except FileNotFoundError:
        return []
    entries = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = parse_words(line.split(" "))
        except LogError as exc:
            raise LogError(f"{path}:{number}: {exc}") from None
        if entry["kind"] == "attempt":
            raise LogError(f"{path}:{number}: an attempt is never logged")
        entries.append(entry)
    return entries


def read_attempt(path):
    if not path or path == "-":
        return None
    try:
        with open(path, encoding="ascii") as f:
            text = f.read().strip()
    except FileNotFoundError:
        return None
    entry = parse_words(text.split(" "))
    if entry["kind"] != "attempt":
        raise LogError(f"{path}: not an attempt")
    return entry


def stack(entries):
    """The releases in force, oldest first: a release pushes, a rollback pops back to the
    release it names."""
    out = []
    for entry in entries:
        if entry["kind"] == "release":
            out.append(entry)
            continue
        while out and out[-1]["tag"] != entry["tag"]:
            out.pop()
        if not out:
            raise LogError(f"a rollback to {entry['tag']}, which is not a release in force")
    return out


def _when(entry):
    try:
        return datetime.fromisoformat(entry["time"])
    except ValueError:
        raise LogError(f"a line of {entry['tag']} has no ISO 8601 time") from None


def top_run(releases):
    """(the lines of the newest release's tag on top of the stack, oldest first; the releases
    below them). One build logged twice in a row is one release (D1)."""
    i = len(releases)
    while i and releases[i - 1]["tag"] == releases[-1]["tag"]:
        i -= 1
    return releases[i:], releases[:i]


def open_attempt(releases, attempt):
    """The attempt, unless it is the newest logged release's own build deployed again."""
    if attempt is None or (releases and releases[-1]["tag"] == attempt["tag"]):
        return None
    return attempt


def plan(entries, attempt, running, restore=False):
    """What ``staging.sh rollback`` does: a dict, or LogError with the reason it refuses.

    An open attempt (a release or deploy that was never logged) is what is left, for the
    release logged last: whether its build already runs, or it stopped before its start and
    the logged release still runs (then only the holds and the window go; its restore point
    is the one a restore uses, D2/O3). Otherwise the newest logged release is left for the one
    below it. The running build may also be the target already: a rollback that stopped
    before it was logged is run again. With an open attempt it may also be an earlier,
    never-logged build of the same window (a release over an unfinished one that stopped
    before its own start; D3/O4): accepted for a restore to the window's point, and without one
    only if nothing of the window ran on the database.

    ``staging.sh rollback`` re-points every image of the stack (Postgres and Keycloak too) to
    the target's: deploy.sh already moved this stack's tags to the build being left (D2/O3/D7).
    Without a restore that is only allowed where the database already runs on the target's
    Postgres and Keycloak: the left build never ran (migrations=none), or the images are equal."""
    if not TAG_RE.match(running):
        raise LogError("the running build has no release tag")
    releases = stack(entries)
    if not releases:
        raise LogError("the release log is empty: nothing to roll back to")
    run, below = top_run(releases)
    attempt = open_attempt(releases, attempt)
    if attempt is not None:
        if entries and _when(attempt) < _when(entries[-1]):
            raise LogError(
                f"releases/attempt ({attempt['tag']}, {attempt['time']}) is older than the log's last "
                f"line ({entries[-1]['time']}): it is no release in progress. Check it, then remove it by hand"
            )
        leave, lines, target = attempt, [attempt], releases[-1]
        note = "the unfinished release (never logged)"
    else:
        if not below:
            raise LogError(f"no logged release before {run[-1]['tag']}: nothing to roll back to")
        leave, lines, target = run[-1], run, below[-1]
        note = "the running release"
    if target["tag"] == leave["tag"]:
        raise LogError(f"{leave['tag']} would be rolled back to itself: refused")
    other = running not in (leave["tag"], target["tag"])
    if other:
        # D3/O4: a release of another build over an unfinished one that stopped before its own
        # start leaves an earlier build of the same window running (never logged; rp_from names
        # the one whose restore point the window holds). A restore to the window's point replaces
        # whatever runs. Without one, only when nothing of the window ran on the database
        # (migrations=none, no point carried over) and the fingerprints allow it (below).
        rp_from = attempt.get("rp_from") if attempt is not None else None
        carried = attempt is not None and attempt.get("restore_point", "-") not in ("", "-")
        if attempt is None:
            raise LogError(
                f"the running build {running} is neither {leave['tag']} ({note}) nor {target['tag']} "
                "(the release before it): roll forward, or start the logged release by hand"
            )
        if restore and not carried:
            raise LogError(
                f"the running build {running} is neither {leave['tag']} ({note}) nor {target['tag']}, "
                f"and {leave['tag']} holds no restore point to go back to: fix forward"
            )
        if not restore and (carried or rp_from or leave.get("migrations") != "none"):
            raise LogError(
                f"the running build {running} is neither {leave['tag']} ({note}) nor {target['tag']}: "
                f"an earlier build of this window ({rp_from or running}) may have migrated the database. "
                + ("Roll back with --restore (the window's restore point)" if carried else "Fix forward")
            )
        whose = ", whose restore point the window holds" if running == rp_from else ""
        note += f"; {running} runs (an earlier build of this window{whose}, never logged)"
    if running == target["tag"]:
        note += f"; {target['tag']} runs already: its holds, window and checks are done again"
    result = {"leave": leave["tag"], "leave_note": note, "target": target["tag"], "running": running}
    for key in IMAGES:
        if key in target:
            result[f"target_{key}"] = target[key]
    if restore:
        line = next((e for e in lines if e.get("restore_point", "-") not in ("", "-")), None)
        if line is None:
            why = "no backups, or it was started without a window"
            if leave.get("migrations") == "none":
                why = "it stopped before its restore point, and nothing was migrated: roll back without --restore"
            raise LogError(f"{leave['tag']} took no restore point ({why}): nothing to restore to")
        result.update(
            restore_point=line["restore_point"],
            lsn=line.get("lsn", "-"),
            rp_time=line.get("rp_time", "-"),
            rp_of=line.get("rp_from", line["tag"]),
        )
        return result
    if leave.get("migrations") == "none" and not other:
        # The release stopped before its restore point: nothing of its build ran on the database
        return result
    reasons = []
    for key, what in (
        ("tenant_fp", "the hospitals' schema (tenant migrations)"),
        ("public_fp", "the platform's schema (public migrations)"),
        ("keycloak", "the Keycloak image (its database migrates only forward)"),
        ("postgres", "the Postgres image"),
    ):
        if leave.get(key) != target.get(key):
            reasons.append(f"{what} changed between {target['tag']} and {leave['tag']}")
    if reasons:
        raise LogError(
            "a rollback without a restore is refused: "
            + "; ".join(reasons)
            + ". Fix forward, or roll back with --restore (loses what was written since the "
            "restore point; deploy/staging/README.md, Releases)"
        )
    return result


def point_used(entries, attempt, name):
    """True if a line of the log (or the attempt) already names that restore point (D4)."""
    lines = [*entries, *([attempt] if attempt else [])]
    return any(name in (e.get("restore_point"), e.get("restored")) for e in lines)


def keep(entries, attempt, n, running=None):
    """Release tags whose images are kept: the last ``n`` distinct tags of the log (newest
    first), the release running now and the one in progress."""
    tags = []
    for entry in reversed(entries):
        if entry["tag"] not in tags:
            tags.append(entry["tag"])
        if entry["kind"] == "rollback" and entry["from"] not in tags and len(tags) < n:
            tags.append(entry["from"])
        if len(tags) >= n:
            break
    tags = tags[:n]
    for extra in (attempt["tag"] if attempt else None, running):
        if extra and TAG_RE.match(extra) and extra not in tags:
            tags.append(extra)
    return tags


def needs_release(entries, tenant_fp, public_fp, db_created, attempt=None):
    """None if a plain start is enough, else the reason a release (the window) is needed."""
    releases = stack(entries)
    attempt = open_attempt(releases, attempt)
    if attempt is not None and (attempt.get("via") == "release" or attempt.get("restore_point", "-") != "-"):
        # A plain start would overwrite its record and restore point (D3/O4)
        return (
            f"release {attempt['tag']} is unfinished (releases/attempt: its window, holds and restore "
            "point): finish it (release --finish), roll back, or release this build in the window "
            "(it keeps that restore point)"
        )
    if not releases:
        if db_created:
            return "no release is logged on a server with a database (the first P1-T18 release is a full stop)"
        return None
    current = releases[-1]
    changed = [
        what
        for key, value, what in (
            ("tenant_fp", tenant_fp, "the hospitals' schema"),
            ("public_fp", public_fp, "the platform's schema"),
        )
        if current.get(key) != value
    ]
    if changed:
        return f"this build changes {' and '.join(changed)} (migrations)"
    return None


def main(argv):
    if len(argv) < 2:
        raise LogError(__doc__)
    cmd, args = argv[1], argv[2:]
    if cmd == "tag" and len(args) == 3:
        print(release_tag(*args))
    elif cmd == "check-line" and args:
        parse_words(args)
    elif cmd == "line" and args:
        print(format_line(parse_words(args)))
    elif cmd == "state" and 1 <= len(args) <= 2:
        for entry in stack(read_log(args[0])):
            print(entry["tag"])
        attempt = read_attempt(args[1] if len(args) == 2 else None)
        if attempt:
            print(f"{attempt['tag']} (in progress)")
    elif cmd == "plan" and len(args) in (3, 4) and args[3:] in ([], ["--restore"]):
        result = plan(read_log(args[0]), read_attempt(args[1]), args[2], restore=bool(args[3:]))
        for key, value in result.items():
            print(f"{key}={value}")
    elif cmd == "keep" and len(args) in (3, 4):
        for tag in keep(read_log(args[0]), read_attempt(args[1]), int(args[2]), *args[3:]):
            print(tag)
    elif cmd == "needs-release" and len(args) == 5:
        reason = needs_release(read_log(args[0]), args[2], args[3], args[4] == "1", read_attempt(args[1]))
        if reason:
            print(reason)
            return 3
    elif cmd == "point-free" and len(args) == 3:
        if point_used(read_log(args[0]), read_attempt(args[1]), args[2]):
            print(f"restore point {args[2]} is already in the release log")
            return 1
    else:
        raise LogError(f"usage: see {argv[0]} (no such command or wrong arguments: {cmd})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except LogError as exc:
        print(f"release log: {exc}", file=sys.stderr)
        sys.exit(1)
