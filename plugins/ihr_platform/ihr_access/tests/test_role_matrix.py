"""The role x endpoint matrix and the clinical canary (P1-T11b3; design-b.md §6, "After the
attack" §9, §10 items 12-19). How routes are enumerated, filled and classified: matrix.py.

One GET crawl per class (each answer rolled back), shared by the tests below; the write crawl
is its own. With ``IHR_MATRIX_DUMP=1`` every answer is printed (``@@M``, ``@@P``, ``@@W``,
``@@K``, ``@@R`` lines; ``scripts/care-tenancy.sh`` passes the variable into the container,
docs/UPSTREAM.md gives the command) so the reviewed files can be compared or rebuilt; a cell is
never classified as allowed without reading why (design: a restricted role reaching clinical
data is a defect).
"""

import datetime
import inspect
import os
import re
import sys
import time
from unittest import mock

from django.conf import settings
from django.db import transaction
from django.test import SimpleTestCase
from ihr_identity import testing as identity
from ihr_roles import registry

from ihr_access import guard, records, testing
from ihr_access.testing import build_world
from ihr_access.tests import matrix
from ihr_access.tests.base import SwapCase

DUMP = bool(os.environ.get("IHR_MATRIX_DUMP"))
# Guarded spec classes no matrix route serializes for a restricted user, each with why
UNREACHED_SPECS = {
    "DeviceRetrieveSpec": "devices are closed to users of our roles (branches.closed): an "
    "empty list and 403; test_guard.BedAndDevice serializes the spec itself",
    "TokenBookingBaseReadSpec": "the base of the booking shapes; CARE serializes its "
    "subclasses",
    "TokenBookingRetrieveSpec": "a booking's own screen is CARE's (can_list_booking), not "
    "projected; our branch (a patient's bookings) serializes TokenBookingReadSpec",
    "TokenBookingOTPReadSpec": "the patient OTP flow only (P1-T20a)",
}


def alias(role):
    return matrix.ALIASES.get(role, role)


def say(line):
    sys.stdout.write(line + "\n")


HEALTH_QUEUE = "ihr_test_matrix_celery"  # never written: LLEN is always 0


def empty_health_queue():
    """CARE's /health/ queue check reads the live broker's ``celery`` queue (review O3): point it
    at an empty queue on the test Redis, so the health rows never depend on the dev stack."""
    from healthy_django.conf import HEALTH_CHECK

    (check,) = (c for c in HEALTH_CHECK if c.slug == "celery_queue_length")
    return mock.patch.dict(
        check.params,
        {"broker": settings.CACHES["default"]["LOCATION"], "queue_name": HEALTH_QUEUE},
    )


class MatrixCase(SwapCase):
    """The full world (testing.add_matrix_world) and the crawls, once per class."""

    _crawl = None
    _writes = None

    @classmethod
    def setUpTestData(cls):
        cls.world = build_world(full=True)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._health = empty_health_queue()
        cls._health.start()

    @classmethod
    def tearDownClass(cls):
        cls._health.stop()
        super().tearDownClass()

    def viewers(self):
        users = {short: self.world.users[name] for short, name in matrix.ROLES.items()}
        users |= {
            short: self.world.users[name] for short, name in matrix.ALIAS_USERS.items()
        }
        # MRD with a live records grant on the first patient (P1-T11b4)
        users |= {
            short: self.world.users[name] for short, name in matrix.GRANTED.items()
        }
        return users

    def crawl(self):
        cls = type(self)
        if cls._crawl is None:
            rows = matrix.read_matrix()
            start = time.monotonic()
            with identity.fake_keycloak():
                clients = {
                    role: self.signed_in(user) for role, user in self.viewers().items()
                }
                found = matrix.Crawl(self.world).run(clients, rows)
            found.seconds = time.monotonic() - start
            say(
                f"\n[role-matrix] GET crawl: {found.requests} requests (GET and POST "
                f"reads), {len({k for k, _ in found.answers})} route keys, "
                f"{len({k for k, _ in found.posts})} POST reads, "
                f"{len(found.unfilled)} unfilled, {found.seconds:.1f} s"
            )
            if DUMP:
                for tag, answers in (("M", found.answers), ("P", found.posts)):
                    for (key, role), a in sorted(answers.items()):
                        say(
                            f"@@{tag}|{key}|{role}|{a.outcome}|{a.status}|"
                            f"{','.join(sorted(a.markers))}|"
                            f"{','.join(sorted({s for s, _ in a.shapes}))}|"
                            f"{','.join(sorted(a.identity))}"
                        )
                        for path in sorted(a.keys):
                            say(f"@@K|{key}|{role}|{path}")
                for key in sorted(found.unfilled):
                    say(f"@@U|{key}")
                for key, keys in sorted(found.reads.items()):
                    say(f"@@R|{key}|{','.join(sorted(keys))}")
            cls._crawl = found
        return cls._crawl

    def writes(self):
        cls = type(self)
        if cls._writes is None:
            rows = matrix.read_matrix()
            answers, unfilled, skip_filled, requests = {}, set(), set(), 0
            start = time.monotonic()
            # a refused records grant writes its log line (records.emit): not into the test log
            with (
                identity.fake_keycloak(),
                mock.patch.object(records, "emit", lambda line: None),
            ):
                clients = {
                    role: self.signed_in(user) for role, user in self.viewers().items()
                }
                for key, method, path in matrix.write_targets(self.world, rows):
                    row = matrix.write_rows(rows, key, method)
                    if path is None:
                        unfilled.add(key)
                        continue
                    if set(row.outcomes.values()) == {"skip"}:
                        skip_filled.add(f"{method.upper()} {key}")  # review M5
                        continue
                    for role, client in clients.items():
                        if row.outcomes[alias(role)] != "deny":
                            continue
                        with transaction.atomic():
                            response = client.generic(
                                method.upper(),
                                path,
                                "{}",
                                content_type="application/json",
                            )
                            transaction.set_rollback(True)
                        requests += 1
                        answers[(key, method, role)] = response.status_code
            seconds = time.monotonic() - start
            say(
                f"\n[role-matrix] write crawl: {requests} requests, "
                f"{len(unfilled)} unfilled routes, {seconds:.1f} s"
            )
            if DUMP:
                for (key, method, role), code in sorted(answers.items()):
                    say(f"@@W|{key}|{method}|{role}|{code}")
                for key in sorted(unfilled):
                    say(f"@@WU|{key}")
            cls._writes = (answers, unfilled, skip_filled)
        return cls._writes


class RoleMatrix(MatrixCase):
    maxDiff = None

    def test_role_matrix(self):
        """Every GET route key, as every matrix role, answers as role_matrix.txt says; a route
        not in the file, or a row for no route key, fails until it is reviewed. ``skip`` is
        only for a route fill() cannot fill: a skip row on a filled route is requested and
        fails (review M5)."""
        crawl = self.crawl()
        rows = {
            key: row for (m, key), row in matrix.read_matrix().items() if m == "GET"
        }
        seen = {key for key, _ in crawl.answers}
        every = seen | crawl.unfilled
        self.assertEqual(
            sorted(every - set(rows)),
            [],
            "route keys not classified in role_matrix.txt",
        )
        self.assertEqual(
            sorted(set(rows) - every), [], "role_matrix.txt rows for no route key"
        )
        for key in sorted(crawl.unfilled):
            with self.subTest(key=key):
                self.assertEqual(
                    set(rows[key].outcomes.values()), {"skip"}, "fill() cannot fill it"
                )
        for (key, role), answer in sorted(crawl.answers.items()):
            with self.subTest(key=key, role=role):
                expected = rows[key].outcomes[alias(role)]
                self.assertNotEqual(expected, "skip", "a skip row on a filled route")
                self.assertEqual(answer.outcome, expected, f"status {answer.status}")

    def test_post_reads(self):
        """post_reads.txt (review K3): the reads CARE makes by POST, with real bodies, answer
        every role as the file says; the canary, its exemptions, the Owner rule and the key
        snapshot read these answers too. Each route of a role_matrix.txt row whose reason says
        "a read by POST" has a line, and each line's route has such a row."""
        crawl = self.crawl()
        reads = matrix.read_post_reads()
        for read in reads:
            for viewer in self.viewers():
                with self.subTest(key=read.key, role=viewer):
                    answer = crawl.posts[(read.key, viewer)]
                    self.assertEqual(
                        answer.outcome,
                        read.outcomes[alias(viewer)],
                        f"status {answer.status}",
                    )
        marked = {
            key
            for (method, key), row in matrix.read_matrix().items()
            if method == "POST" and "a read by POST" in row.reason
        }
        self.assertEqual(sorted(marked - {r.route for r in reads}), [], "no post read")
        self.assertEqual(sorted({r.route for r in reads} - marked), [], "no POST row")

    def test_every_method_of_denied_routes(self):
        """Every non-GET method of a class view, as each of our roles whose write row is
        ``deny``, with an empty body: never 2xx, never a server error. An ``allow`` write row
        is not sent: it names its tests (MatrixFiles). How each was refused is snapshotted in
        role_matrix_writes.txt (review M3): ``auth`` (403/404/401/405) or ``body`` (400: CARE
        validated first); a new write route, a gone one, or a cell that moves between them
        fails until reviewed. A skip row on a route fill() fills fails (review M5)."""
        answers, unfilled, skip_filled = self.writes()
        rows = matrix.read_matrix()
        self.assertEqual(sorted(skip_filled), [], "skip rows on filled write routes")
        for key in sorted(unfilled):
            with self.subTest(key=key):
                row = matrix.write_rows(rows, key)
                self.assertEqual(
                    set(row.outcomes.values()), {"skip"}, "fill() cannot fill it"
                )
        known = {key for key, _method, _path in matrix.write_targets(self.world, rows)}
        stale = sorted(
            f"{method} {key}"
            for method, key in rows
            if method != "GET" and key not in known
        )
        self.assertEqual(stale, [], "write rows for no route")
        sent = 0
        found = {}
        for (key, method, role), code in sorted(answers.items()):
            sent += 1
            found.setdefault((method.upper(), key), {})[role] = matrix.write_class(code)
            with self.subTest(key=key, method=method, role=role):
                self.assertFalse(200 <= code < 300, f"{code}: a write was accepted")
                self.assertLess(code, 500, "a server error can hide a write")
        self.assertGreater(sent, 1000)
        snapshot = matrix.read_writes()
        if DUMP:
            for (method, key), cells in sorted(found.items()):
                kinds = {}
                for role, kind in cells.items():
                    kinds.setdefault(kind, []).append(role)
                tokens = " ".join(
                    f"{kind}:{','.join(sorted(r))}" for kind, r in sorted(kinds.items())
                )
                say(f"@@S|{method} {key}  {tokens}")
        self.assertEqual(
            sorted(f"{m} {k}" for m, k in set(found) - set(snapshot)),
            [],
            "write routes not in role_matrix_writes.txt",
        )
        self.assertEqual(
            sorted(f"{m} {k}" for m, k in set(snapshot) - set(found)),
            [],
            "role_matrix_writes.txt rows for no denied write",
        )
        for (method, key), cells in sorted(found.items()):
            with self.subTest(write=f"{method} {key}"):
                self.assertEqual(cells, snapshot[(method, key)], "a write cell moved")

    def test_clinical_canary_never_reaches_restricted_roles(self):
        """No answer to any of our roles (MRD without a grant included) holds a marker of the
        world's clinical fields, except where canary_exemptions.txt allows that marker on that
        route; the Owner sees no patient's name, phone number or id anywhere either. GET and
        POST-read answers alike. MRD with a grant on the first patient (mrdg, P1-T11b4) is
        read too: its exemptions are that patient's own records, each with a negative row on
        the other patient's."""
        crawl = self.crawl()
        exemptions = matrix.read_exemptions()
        for (key, viewer), answer in sorted(crawl.every_answer().items()):
            role = alias(viewer)
            if role not in matrix.CANARY_ROLES:
                continue
            allowed = {
                e.marker
                for e in exemptions
                if e.verb == "allow" and e.role == role and e.matches(key)
            }
            with self.subTest(key=key, role=viewer):
                self.assertEqual(sorted(answer.markers - allowed), [], "markers leaked")
                if role == "owner":
                    self.assertEqual(sorted(answer.identity), [], "patient identity")

    def test_every_marker_is_reachable_by_care_roles(self):
        """The canary's positive control: each marker the world holds reaches the Doctor or the
        Facility Admin through some route, so its absence elsewhere means something."""
        crawl = self.crawl()
        seen = set()
        for (_key, role), answer in crawl.every_answer().items():
            if role in matrix.CARE:
                seen |= answer.markers
        self.assertEqual(sorted(testing.MARKERS - seen), [])

    def test_canary_exemptions_are_narrow(self):
        """Each ``allow`` exemption is used, and has a negative row (``never``: the same marker
        for any role, or the same role and routes); each negative row is requested as its role,
        and its marker reaches a care role on those routes (so its absence is real)."""
        crawl = self.crawl()
        every = crawl.every_answer()
        exemptions = matrix.read_exemptions()
        allows = [e for e in exemptions if e.verb == "allow"]
        nevers = [e for e in exemptions if e.verb == "never"]
        for e in allows:
            with self.subTest(line=e.line, role=e.role, marker=e.marker):
                used = [
                    key
                    for (key, role), a in every.items()
                    if alias(role) == e.role
                    and e.matches(key)
                    and e.marker in a.markers
                ]
                self.assertTrue(used, "exemption not used: remove it")
                self.assertTrue(
                    any(
                        n.marker == e.marker
                        or (n.role == e.role and n.pattern == e.pattern)
                        for n in nevers
                    ),
                    "no negative row",
                )
        for n in nevers:
            with self.subTest(line=n.line, role=n.role, marker=n.marker):
                hits = [
                    (key, a)
                    for (key, role), a in every.items()
                    if alias(role) == n.role and n.matches(key)
                ]
                self.assertTrue(hits, "no matching route was requested as this role")
                self.assertFalse(
                    [k for k, a in hits if n.marker in a.markers], "marker present"
                )
                self.assertTrue(
                    any(
                        role in matrix.CARE and n.matches(key) and n.marker in a.markers
                        for (key, role), a in every.items()
                    ),
                    "no care role sees this marker on these routes",
                )
                self.assertFalse(
                    any(
                        e.role == n.role and e.marker == n.marker and e.matches(k)
                        for e in allows
                        for k, _a in hits
                    ),
                    "an allow row covers this negative row",
                )

    def test_response_keys_match_the_snapshot(self):
        """role_matrix_keys.txt: every key (nested dicts walked) of every allowed or projected
        answer to a role of ours, the Radiologist included in its own column (review K4). An
        embedded guarded shape is one ``<shape>`` entry only where the guard decided on that
        instance; anything else is walked. A new key fails until it is reviewed (A12: account,
        charge, invoice, payment, booking, token and delivery bodies)."""
        crawl = self.crawl()
        found = {}
        for (key, role), answer in crawl.every_answer().items():
            if alias(role) not in matrix.OURS:
                continue
            for path in answer.keys:
                found.setdefault((key, path), set()).add(alias(role))
        snapshot = matrix.read_keys()
        new = sorted(
            f"{key} {path} {','.join(sorted(roles - snapshot.get((key, path), set())))}"
            for (key, path), roles in found.items()
            if roles - snapshot.get((key, path), set())
        )
        gone = sorted(
            f"{key} {path}"
            for (key, path), roles in snapshot.items()
            if roles - found.get((key, path), set())
        )
        self.assertEqual(new[:40], [], f"{len(new)} keys not in role_matrix_keys.txt")
        self.assertEqual(gone[:40], [], f"{len(gone)} snapshot keys no longer seen")
        self.assertTrue(
            any("rad" in roles for roles in found.values()), "the Radiologist's column"
        )

    def test_world_covers_every_guarded_shape(self):
        """Every class the guard sits on (and each subclass) is projected for a restricted
        role somewhere in the crawl, or is listed in UNREACHED_SPECS with why."""
        crawl = self.crawl()
        projected = set()
        for (_key, role), answer in crawl.every_answer().items():
            if alias(role) in matrix.RESTRICTED:
                projected |= {cls for _shape, cls in answer.shapes}
        for shape in guard.SHAPES.values():
            for cls in guard.classes_of(guard.import_string(shape.base)):
                with self.subTest(shape=shape.name, spec=cls.__name__):
                    if cls.__name__ in UNREACHED_SPECS:
                        self.assertNotIn(cls.__name__, projected, "now reached: unlist")
                    else:
                        self.assertIn(cls.__name__, projected)

    def test_targets_are_unique_and_skip_means_unfilled(self):
        """Review O6: each route key is requested once (our invitation swap and CARE's router
        both give /api/v1/users/). Review M5: a skip row on a route fill() fills does not drop
        it from the crawl (it is requested, and its answer fails test_role_matrix)."""
        rows = matrix.read_matrix()
        key = "/api/v1/facility/{external_id}/"
        rows[("GET", key)] = matrix.Row(
            "GET", key, dict.fromkeys(matrix.GROUPS["all"], "skip"), "a test skip"
        )
        crawl = matrix.Crawl(self.world)
        keys = [k for k, _base, _path, _query in crawl.targets(rows)]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(keys.count("/api/v1/users/"), 1)
        self.assertIn(key, keys)
        self.assertNotIn(key, crawl.skipped)
        writes = matrix.write_targets(self.world, rows)
        pairs = [(k, m) for k, m, _p in writes]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_the_health_probe_reads_an_empty_test_queue(self):
        """Review O3: inside the matrix classes CARE's queue check reads an empty queue on the
        test Redis, not the dev stack's broker."""
        from healthy_django.conf import HEALTH_CHECK

        (check,) = (c for c in HEALTH_CHECK if c.slug == "celery_queue_length")
        self.assertEqual(check.params["queue_name"], HEALTH_QUEUE)
        self.assertEqual(check.params["broker"], settings.CACHES["default"]["LOCATION"])
        # and it answers from there: the empty test queue, a 200 (a Redis outage would be a 500,
        # which matches no row, so it fails the matrix loudly; the runner's own Redis audit and
        # every cache need the same server)
        self.assertEqual(check.check(), (200, {"queue_length": 0}))

    def test_every_query_read_has_a_params_line(self):
        """Review M2/K2/O1, NEW-2: every query key the code of CARE or ours read on a route
        (helpers, mixins, parents, detail routes and actions included; paging keys aside) is
        named by a list_params.txt line of that route: a variant's key, or a ``none:`` line's
        reviewed key (a bare ``none`` names none, so a new key fails until reviewed). A route
        under a patient reading ``encounter`` is covered by the generated
        ``?encounter=encounter_b`` variant. The POST reads (post_reads.txt) are recorded too, as
        ``POST <route>``: their keys are named by a post_reads.txt assignment or a ``none:``
        line of the route."""
        crawl = self.crawl()
        params = matrix.read_params()
        posted = {}
        for read in matrix.read_post_reads():
            posted.setdefault(read.route, set()).update(
                matrix.named_keys([read.assignment]) if read.assignment else set()
            )
        missing = {}
        for key, keys in crawl.reads.items():
            wanted = set(keys) - matrix.PAGING_KEYS
            route = key.removeprefix("POST ")
            named = matrix.named_keys(params.get(route, []))
            if key.startswith("POST "):
                named |= posted.get(route, set())
            elif matrix.PATIENT_PARAM in key:
                wanted.discard("encounter")
            if left := wanted - named:
                missing[key] = sorted(left)
        self.assertEqual(
            missing,
            {},
            "query keys read on a route that no list_params.txt line names",
        )
        self.assertTrue(
            any("encounter" in keys for keys in crawl.reads.values()),
            "the read log saw nothing: is the recorder installed?",
        )
        self.assertTrue(
            any(k.startswith("POST ") for k in crawl.reads),
            "the POST reads were not recorded",
        )


class MatrixFiles(SimpleTestCase):
    """The reviewed files parse, and what they name exists."""

    SCOPED = re.compile(
        r"service_request/\{external_id\}|diagnostic_report|\{patient_external_id\}"
        r"|/specimen/\{external_id\}/"
    )
    GRANTS = "ihr_roles.tests.test_grants.ExactGrants."
    # allowed writes whose answer names a patient or a booking (review M4): sent with a valid
    # body, as every role of ours, by DeskWrites (a read by POST is post_reads.txt's)
    DESK = re.compile(
        r"/(appointments|token|account|invoice|payment_reconciliation)/"
        r"|/slots/\{external_id\}/create_appointment/"
    )

    def test_the_files_parse_and_allowed_writes_name_their_tests(self):
        rows = matrix.read_matrix()
        for (method, key), row in rows.items():
            with self.subTest(method=method, key=key):
                values = set(row.outcomes.values())
                if "skip" in values:
                    self.assertEqual(values, {"skip"}, "skip is for every role or none")
                if method != "GET":
                    for role, outcome in row.outcomes.items():
                        if outcome == "care":
                            self.assertIn(role, matrix.CARE, "only CARE's roles: care")
                    if "allow" in values:
                        self.assertTrue(row.tests, "an allowed write names its tests")
                        if self.SCOPED.search(key):
                            self.assertTrue(
                                [t for t in row.tests if not t.startswith(self.GRANTS)],
                                "a write on a request, report or patient-scoped route names "
                                "a test that sends it, not only the grants (review M4)",
                            )
                        if self.DESK.search(key) and "a read by POST" not in row.reason:
                            self.assertIn(
                                DESK_TEST,
                                row.tests,
                                "a desk write names DeskWrites, which sends it with a body "
                                "(review M4)",
                            )
                for label in row.tests:
                    self.assertTrue(matrix.resolve_test(label), f"no test {label}")
        matrix.read_params()
        matrix.read_exemptions()
        matrix.read_keys()
        matrix.read_writes()
        matrix.read_post_reads()

    def test_a_new_optional_key_is_caught(self):
        """Review NEW-2: a key read on a route whose only line is ``none:`` with other keys (or
        a bare ``none``) is reported; the line's own keys are not."""
        self.assertEqual(matrix.named_keys(["none"]), set())
        self.assertEqual(matrix.named_keys(["none:mine,parent"]), {"mine", "parent"})
        self.assertEqual(
            matrix.named_keys(["?{external_id}=lab_location&location=ward&q~x"]),
            {"location", "q"},
        )
        self.assertIsNone(matrix.NONE_RE.fullmatch("none:"))

    def test_an_unhandled_patient_like_body_is_walked(self):
        """Review K4: a nested body with a patient's keys is one ``<patient>`` entry only when
        the guard decided on that instance; otherwise its keys are walked."""
        body = {
            "subject": {
                "id": "p-1",
                "blood_group": "x",
                "gender": "x",
                "year_of_birth": 1,
                "secret": "x",
            },
            "staff": {"username": "u", "first_name": "a", "last_name": "b", "x": 1},
        }
        walked = matrix.key_paths(body)
        self.assertIn("subject.secret", walked)
        self.assertIn("staff<user>", walked)
        handled = matrix.key_paths(body, handled=frozenset({("patient", "p-1")}))
        self.assertEqual(handled, {"subject<patient>", "staff<user>"})

    def test_date_keys_are_one_placeholder(self):
        """Review C-C3: availability_stats answers one key per asked day; the walk records
        ``<date>`` so the snapshot holds on any day. Other keys keep their names."""
        today = {"2026-11-15": {"booked_slots": 0}, "2026-11-16": {"total_slots": 1}}
        later = {"2026-11-22": {"booked_slots": 0}, "2026-11-23": {"total_slots": 1}}
        self.assertEqual(
            matrix.key_paths(today),
            {"<date>", "<date>.booked_slots", "<date>.total_slots"},
        )
        self.assertEqual(matrix.key_paths(today), matrix.key_paths(later))
        self.assertEqual(
            matrix.key_paths({"x": {"2026-11-15T10:00": 1, "20261115": 2}}),
            {"x", "x.2026-11-15T10:00", "x.20261115"},
        )

    def test_the_roles_are_the_registrys(self):
        """Review M6: the matrix crawls every role of ours the registry defines (a new one
        fails here until it has a world user, a short name and a group), its restricted group is
        registry.RESTRICTED, and its comparison roles are CARE's. MRD's hidden grant role is
        b4's (P1-T11b4: a records-grant viewer)."""
        named = {short: matrix.ROLES[short] for short in matrix.OURS}
        self.assertEqual(
            {matrix.ROLES[r] for r in matrix.RESTRICTED}, set(registry.RESTRICTED)
        )
        self.assertEqual(set(named.values()), set(registry.EIGHT))
        self.assertNotIn(registry.HIDDEN, matrix.ROLES.values())
        for short in matrix.CARE:
            self.assertNotIn(matrix.ROLES[short], registry.PERMANENT_NAMES)
        self.assertEqual(set(matrix.GROUPS["all"]), set(matrix.ROLES))
        # The hidden role is crawled as MRD with a live grant (P1-T11b4): its own column,
        # never in a group, expecting mrd's answer where a row does not name it
        self.assertEqual(set(matrix.GRANTED), {"mrdg"})
        for members in matrix.GROUPS.values():
            self.assertNotIn("mrdg", members)
        self.assertEqual(
            matrix.expand(["all:deny", "care:allow"], "test"),
            {**dict.fromkeys(matrix.GROUPS["all"], "deny"), "doctor": "allow",
             "admin": "allow", "mrdg": "deny"},
        )  # fmt: skip
        self.assertEqual(
            matrix.expand(["all:deny", "mrdg:allow"], "test")["mrdg"], "allow"
        )
        self.assertNotIn("mrdg", matrix.expand(["restricted:x"], "test", inherit=False))


class ListParams(SimpleTestCase):
    """§10 item 12, widened by review M2/O1: every GET route whose view class reads the query
    string anywhere in its own or inherited code under ``care.`` or ``ihr_`` (helpers such as
    ``authorize_read_encounter``, mixins, the CARE parent behind one of our swaps), detail
    routes and actions included, has a row in list_params.txt (its parameters, or ``none``
    with why). A static backstop: the matrix's runtime read check
    (``RoleMatrix.test_every_query_read_has_a_params_line``) sees what each route reads. A
    route under a patient is covered by the generated ``?encounter=encounter_b`` variant; a
    route fill() cannot fill (``skip``) is never requested."""

    READS = re.compile(r"request\.(GET|query_params)\b")
    # a read whose key is a literal: .get("k") / .getlist("k") / .pop("k"), ["k"], "k" in ...
    AFTER = re.compile(r"\s*(?:\.\s*(?:get|getlist|pop)\(\s*|\[\s*)[\"'](\w+)[\"']")
    BEFORE = re.compile(r"[\"'](\w+)[\"']\s+(?:not\s+)?in\s+(?:self\.)?$")
    maxDiff = None

    @classmethod
    def static_keys(cls, view):
        """(literal keys, dynamic): the query keys ``view``'s classes under care.*/ihr_* read
        by name, and whether any read names no literal key (``dict(request.GET)``, a variable
        key, the query handed on), which only a reviewed line can cover."""
        keys, dynamic = set(), False
        for klass in view.__mro__:
            module = getattr(klass, "__module__", "") or ""
            if not module.startswith(("care.", "ihr_")):
                continue
            try:
                source = inspect.getsource(klass)
            except (OSError, TypeError):
                continue
            for found in cls.READS.finditer(source):
                after = cls.AFTER.match(source, found.end())
                before = cls.BEFORE.search(
                    source[max(0, found.start() - 80) : found.start()]
                )
                if after:
                    keys.add(after.group(1))
                elif before:
                    keys.add(before.group(1))
                else:
                    dynamic = True
        return keys, dynamic

    @classmethod
    def reading_classes(cls, view):
        """The classes in ``view``'s MRO, from care.* or ihr_* modules, that read the query."""
        found = []
        for klass in view.__mro__:
            module = getattr(klass, "__module__", "") or ""
            if not module.startswith(("care.", "ihr_")):
                continue
            try:
                source = inspect.getsource(klass)
            except (OSError, TypeError):
                continue
            if cls.READS.search(source):
                found.append(klass.__qualname__)
        return found

    def test_every_list_reading_the_query_has_a_row(self):
        params = matrix.read_params()
        rows = matrix.read_matrix()
        routes, gets = {}, set()
        for route, cls, methods in matrix.get_route_views():
            if cls is matrix.Blocked or (methods is not None and "get" not in methods):
                continue
            gets.add(matrix.norm(route))  # function views too (the sign-in flow)
            if cls is not None and methods:
                routes.setdefault(matrix.norm(route), cls)
        needed, unnamed = {}, {}
        for key, cls in routes.items():
            row = rows.get(("GET", key))
            if row is not None and set(row.outcomes.values()) == {"skip"}:
                continue
            if matrix.PATIENT_PARAM in key:
                # review NEW-1: the generated cross-patient variant covers ``encounter`` only;
                # any other key the view's classes read by name (the pharmacist branch's
                # ``facility``) is named by a line, and a read of no literal key needs a line
                keys, dynamic = self.static_keys(cls)
                left = keys - matrix.PAGING_KEYS - {"encounter"}
                left -= matrix.named_keys(params.get(key, []))
                if left:
                    unnamed[key] = sorted(left)
                if dynamic:
                    needed[key] = self.reading_classes(cls)
                continue
            if found := self.reading_classes(cls):
                needed[key] = found
        self.assertEqual(
            {k: v for k, v in needed.items() if k not in params},
            {},
            "routes whose view reads the query with no list_params row",
        )
        self.assertEqual(
            unnamed,
            {},
            "patient routes whose view reads a key besides encounter no line names",
        )
        posted = {read.route for read in matrix.read_post_reads()}
        self.assertEqual(
            sorted(set(params) - gets - posted),
            [],
            "rows for no GET route or POST read",
        )
        for route in sorted(set(params) - gets):
            with self.subTest(route=route):
                self.assertFalse(
                    [a for a in params[route] if matrix.is_variant(a)],
                    "a POST read's line names its keys only (none:...); its variants are "
                    "post_reads.txt lines",
                )

    def test_a_patient_route_reading_another_key_needs_a_line(self):
        """Review NEW-1: the static walk sees the pharmacist branch's ``facility`` (and
        ``encounter``) in EncounterBasedAuthorizationBase, so a patient route of it is not
        covered by the generated encounter variant alone."""
        from care.emr.api.viewsets.medication_request import MedicationRequestViewSet

        keys, _dynamic = self.static_keys(MedicationRequestViewSet)
        self.assertTrue({"facility", "encounter"} <= keys, keys)

    def test_the_walk_reaches_parents_and_helpers(self):
        """The cases the first check missed: CARE's device list behind our swap reads
        ?location (O1), the medication request list reads ?encounter and ?facility in
        EncounterBasedAuthorizationBase (M2)."""
        from care.emr.api.viewsets.medication_request import MedicationRequestViewSet

        from ihr_access.views import IhrDeviceViewSet

        self.assertIn("DeviceViewSet", self.reading_classes(IhrDeviceViewSet))
        self.assertIn(
            "EncounterBasedAuthorizationBase",
            self.reading_classes(MedicationRequestViewSet),
        )


TESTS = "ihr_access.tests.test_role_matrix."
DEPARTMENT_TEST = f"{TESTS}AllowedWrites.test_department_writes_keep_to_their_own_copy"
DESK_TEST = f"{TESTS}DeskWrites.test_desk_writes_with_bodies"
# Markers a write answer may hold beyond canary_exemptions.txt (whose allow rows the GET crawl
# must use: the world's invoice holds no charge, so its GET never shows one)
WRITE_EXEMPTIONS = (
    (
        "billing",
        "charge",
        "/api/v1/facility/{facility_external_id}/invoice/*",
        "an invoice lists its charges by service (Q-K38 A), as the charge list does",
    ),
)


def route_key(url):
    """The role_matrix.txt key of the route ``url`` resolves to."""
    from django.urls import resolve

    return matrix.norm(resolve(url.split("?", 1)[0]).route)


def refused(status, text):
    """Refused by authorization: 401/403/404, or CARE's upsert answer (400) whose every
    datapoint is a permission refusal (it catches PermissionDenied per datapoint)."""
    import json

    if status in (401, 403, 404):
        return True
    if status != 400:
        return False
    try:
        found = json.loads(text)
    except ValueError:
        return False
    return (
        isinstance(found, list)
        and bool(found)
        and all(
            isinstance(i, dict)
            and set(i) == {"detail"}
            and "permission" in str(i["detail"]).lower()
            for i in found
        )
    )


def rows_naming(test, world):
    """{(METHOD, route key, role)}: every allowed write cell of a role of ours whose row names
    ``test`` (a ``W`` row stands for each non-GET method of its route without a row of its
    own)."""
    rows = matrix.read_matrix()
    methods = {}
    for key, method, _path in matrix.write_targets(world, rows):
        methods.setdefault(key, set()).add(method.upper())
    cells = set()
    for (method, key), row in rows.items():
        if method == "GET" or test not in row.tests:
            continue
        sent = (
            {m for m in methods.get(key, ()) if (m, key) not in rows}
            if method == "W"
            else {method}
        )
        for role, outcome in row.outcomes.items():
            if outcome == "allow" and role in matrix.OURS:
                cells |= {(m, key, role) for m in sent}
    return cells


class CheckedWrites(MatrixCase):
    """Writes sent with valid bodies (review M3/M4): as each role of ours its row allows, a 2xx
    whose answer holds no marker beyond the role's exemptions on that route; as each role of
    ours its row denies, a refusal by authorization (401/403/404), never a 2xx or a 400 (a 400
    would leave open whether a valid body is accepted). Each request is rolled back."""

    def setUp(self):
        super().setUp()
        self.sent = set()
        self.exemptions = matrix.read_exemptions()
        self.rows = matrix.read_matrix()

    def allowed_markers(self, role, key):
        found = {
            e.marker
            for e in self.exemptions
            if e.verb == "allow" and e.role == role and e.matches(key)
        }
        for who, name, pattern, _why in WRITE_EXEMPTIONS:
            if role in matrix.expand(
                [f"{who}:x"], "WRITE_EXEMPTIONS"
            ) and matrix.fnmatch.fnmatchcase(key, pattern):
                found.add(name)
        return found

    def viewers_of_ours(self):
        # MRD with a grant too (P1-T11b4): a grant opens reads only, never these writes
        return (*matrix.OURS, *matrix.ALIASES, *matrix.GRANTED)

    def send(self, viewer, method, url, body):
        user = self.world.users[matrix.user_name(viewer)]
        with transaction.atomic():
            answer = getattr(self.api(user), method)(url, body, format="json")
            text = answer.content.decode(errors="replace")
            transaction.set_rollback(True)
        return answer.status_code, text

    def write(self, method, url, body, code=200):
        """Send one write as every role of ours (and the moved Nurse), each as its row says."""
        key = route_key(url)
        row = matrix.write_rows(self.rows, key, method)
        for viewer in self.viewers_of_ours():
            role = alias(viewer)
            outcome = row.outcomes[role]
            with self.subTest(method=method, key=key, role=viewer):
                status, text = self.send(viewer, method, url, body)
                if outcome == "allow":
                    self.assertEqual(status, code, text[:400])
                    self.sent.add((method.upper(), key, role))
                    leaked = matrix.markers_in(text) - self.allowed_markers(role, key)
                    self.assertEqual(sorted(leaked), [], "markers in a write's answer")
                else:
                    self.assertTrue(refused(status, text), f"{status}: {text[:400]}")
                    self.assertNotIn(testing.CANARY, text)

    def assert_every_cell_sent(self, test):
        """Every allowed cell of the rows naming ``test`` was sent (and answered) here."""
        wanted = rows_naming(test, self.world)
        self.assertTrue(wanted, f"no row names {test}")
        self.assertEqual(
            sorted(f"{m} {k} {r}" for m, k, r in wanted - self.sent),
            [],
            "allowed write cells naming this test that it never sent",
        )


class AllowedWrites(CheckedWrites):
    """The named tests of role_matrix.txt's allowed writes that b1/b2's tests do not cover."""

    def test_writes_our_roles_are_granted(self):
        """Writes CARE grants our roles (ExactGrants pins the slugs) answer them, and refuse a
        role without the grant: the Receptionist's booking and queue writes, the billing
        desks' account and charge writes, the Store Keeper's order tags."""
        w = self.world
        o = w.objects
        f = f"/api/v1/facility/{w.facility.external_id}"
        queue = f"{f}/token/queue/{o['token_queue'].external_id}"
        cases = (
            ("Receptionist", "post", f"{f}/appointments/{w.booking.external_id}/set_tags/", {"tags": []}, 200, "Cashier"),
            ("Receptionist", "post", f"{f}/appointments/{w.booking.external_id}/remove_tags/", {"tags": []}, 200, "Cashier"),
            ("Receptionist", "post", f"{queue}/set_primary/", {}, 200, "Cashier"),
            ("Cashier", "post", f"{f}/account/{o['account'].external_id}/rebalance/", {}, 200, "Receptionist"),
            ("TPA Desk", "post", f"{f}/account/{o['account'].external_id}/set_tags/", {"tags": []}, 200, "MRD"),
            ("TPA Desk", "post", f"{f}/account/{o['account'].external_id}/remove_tags/", {"tags": []}, 200, "Owner"),
            ("Cashier", "post", f"{f}/charge_item/{o['charge_item'].external_id}/set_tags/", {"tags": []}, 200, "TPA Desk"),
            ("Cashier", "post", f"{f}/charge_item/{o['charge_item'].external_id}/remove_tags/", {"tags": []}, 200, "TPA Desk"),
            ("Store Keeper", "post", f"{f}/order/delivery/{o['delivery_order'].external_id}/set_tags/", {"tags": []}, 200, "Lab Technician"),
            ("Store Keeper", "post", f"{f}/order/delivery/{o['delivery_order'].external_id}/remove_tags/", {"tags": []}, 200, "Lab Technician"),
            ("Receptionist", "delete", f"{queue}/token/{o['token'].external_id}/", None, 204, "Cashier"),
            ("Receptionist", "delete", f"{queue}/", None, 204, "TPA Desk"),
        )  # fmt: skip
        for role, method, url, body, code, other in cases:
            with self.subTest(role=role, url=url):
                refused = getattr(self.api(other), method)(url, body, format="json")
                self.assertIn(refused.status_code, (403, 404), refused.content[:300])
                answer = getattr(self.api(role), method)(url, body, format="json")
                self.assertEqual(answer.status_code, code, answer.content[:300])
        token = type(o["token"])
        self.assertFalse(token.objects.filter(pk=o["token"].pk).exists())

    def test_department_writes_keep_to_their_own_copy(self):
        """Review M4: the Lab Technician's and the Radiologist's writes on requests and reports
        (granted by CARE's slugs, ExactGrants) answer on their own department's copy and are
        refused on another department's, and what they answer holds no marker of another copy
        or the patient's chart."""
        from care.emr.models import SpecimenDefinition
        from model_bakery import baker

        w = self.world
        o = w.objects
        f = f"/api/v1/facility/{w.facility.external_id}"
        p = f"/api/v1/patient/{w.patient.external_id}"
        definition = baker.make(
            SpecimenDefinition,
            facility=w.facility,
            status="active",
            slug=f"f-{w.facility.external_id}-ihr-matrix",
            title="Viral load tube",
            description="EDTA",
        )
        specimen = {
            "status": "available",
            "specimen_type": {
                "code": "122555007",
                "display": "Venous blood specimen",
                "system": "http://snomed.info/sct",
            },
        }
        lab_section = {**testing.LAB}
        imaging = {**testing.IMAGING}
        own = {"Lab Technician": "lab", "Radiologist": "xray"}
        cases = []
        # the specimen's own writes (review M4: row W specimen/{external_id}/ lab:allow): the lab
        # copy's specimen as the Lab Technician, never the ward copy's
        for copy, key in (("lab", "lab_specimen"), ("ward", "specimen")):
            for method in ("put", "patch"):
                cases.append(
                    (
                        "Lab Technician",
                        copy,
                        method,
                        f"{f}/specimen/{o[key].external_id}/",
                        {"status": "available", "note": "received at the bench"},
                    )
                )
        for copy, request_key, report_key, category in (
            ("lab", "lab_service_request", "lab_diagnostic_report", lab_section),
            ("ward", "service_request", "diagnostic_report", lab_section),
            ("xray", "xray_service_request", "xray_diagnostic_report", imaging),
        ):
            request = o[request_key].external_id
            report = o[report_key].external_id
            if copy != "xray":
                cases += [
                    (
                        "Lab Technician",
                        copy,
                        "post",
                        f"{f}/service_request/{request}/create_specimen/",
                        specimen,
                    ),
                    (
                        "Lab Technician",
                        copy,
                        "post",
                        f"{f}/service_request/{request}/create_specimen_from_definition/",
                        {
                            "specimen_definition": str(definition.external_id),
                            "specimen": specimen,
                        },
                    ),
                ]
            for who in own:
                cases += [
                    (who, copy, "post", f"{p}/diagnostic_report/", {"status": "preliminary", "category": category, "service_request": str(request)}),
                    (who, copy, "put", f"{p}/diagnostic_report/{report}/", {"status": "preliminary", "category": category, "conclusion": "updated"}),
                    (who, copy, "patch", f"{p}/diagnostic_report/{report}/", {"status": "preliminary", "category": category, "conclusion": "updated"}),
                    (who, copy, "post", f"{p}/diagnostic_report/{report}/upsert_observations/", {"observations": []}),
                ]  # fmt: skip
        type(o["diagnostic_report"]).objects.filter(
            pk__in=[
                o[k].pk
                for k in (
                    "diagnostic_report",
                    "lab_diagnostic_report",
                    "xray_diagnostic_report",
                )
            ]
        ).update(
            status="preliminary"
        )  # fmt: skip  (results are refused on a final report)
        short = {name: s for s, name in matrix.ROLES.items()}
        for who, copy, method, url, body in cases:
            row = matrix.write_rows(self.rows, route_key(url), method)
            bystanders = [
                v for v in self.viewers_of_ours() if row.outcomes[alias(v)] != "allow"
            ]
            with self.subTest(who=who, copy=copy, method=method, url=url):
                status, text = self.send(short[who], method, url, body)
                if copy == own[who]:
                    self.assertEqual(status, 200, text[:300])
                    self.sent.add((method.upper(), route_key(url), short[who]))
                    others = {
                        m for m in matrix.markers_in(text) if not m.endswith(f"-{copy}")
                    }
                    self.assertEqual(
                        sorted(others), [], "markers of another copy or the chart"
                    )
                else:
                    self.assertIn(status, (403, 404), text[:300])
                    self.assertNotIn(testing.CANARY, text)
            if copy != own[who]:
                continue
            # the same valid body as every other role of ours: refused by authorization
            # (the write crawl's empty body often stops at CARE's validation, review M3)
            for viewer in bystanders:
                with self.subTest(viewer=viewer, method=method, url=url):
                    status, text = self.send(viewer, method, url, body)
                    self.assertIn(status, (401, 403, 404), text[:300])
                    self.assertNotIn(testing.CANARY, text)
        self.assert_every_cell_sent(DEPARTMENT_TEST)

    def test_preferences_are_the_callers_own(self):
        """Favourites, push settings and preferences change only the caller's own settings;
        the status page shows no hospital data."""
        w = self.world
        doctor = w.users["Doctor"]
        before = doctor.pf_endpoint
        for role in matrix.OURS:
            user = w.users[matrix.ROLES[role]]
            with self.subTest(role=role):
                answer = self.api(user).patch(
                    f"/api/v1/users/{doctor.username}/pnconfig/",
                    {"pf_endpoint": f"https://push.example.test/{role}"},
                    format="json",
                )
                self.assertEqual(answer.status_code, 200, answer.content[:300])
                user.refresh_from_db()
                self.assertEqual(user.pf_endpoint, f"https://push.example.test/{role}")
                slug = w.objects["product_knowledge"].slug
                liked = self.api(user).post(
                    f"/api/v1/product_knowledge/{slug}/add_favorite/", {}, format="json"
                )
                self.assertEqual(liked.status_code, 200, liked.content[:300])
                self.assertEqual(liked.json(), {})
                status = self.api(user).post("/health/statuspage", {}, format="json")
                self.assertEqual(status.status_code, 200)
                self.assertNotIn(str(w.facility.external_id), status.content.decode())
        doctor.refresh_from_db()
        self.assertEqual(doctor.pf_endpoint, before)
        from care.emr.models.favorites import UserResourceFavorites

        self.assertFalse(UserResourceFavorites.objects.filter(user=doctor).exists())


class DeskWrites(CheckedWrites):
    """Review M4/M3: the front desk's and the billing desks' allowed writes (bookings, slots,
    queues and tokens, accounts, invoices, payments) sent with valid bodies, as every role of
    ours: each allowed role gets a 2xx whose answer holds no marker beyond its exemptions (the
    booking's patient is projected, a token or booking note only where the Receptionist's own
    CARE screens show it), every other role a refusal by authorization. MatrixFiles makes each
    allowed write on these routes name this test, and the test sends every such cell."""

    def test_desk_writes_with_bodies(self):
        from care.emr.models.charge_item import ChargeItem
        from care.emr.models.invoice import Invoice
        from care.emr.models.scheduling import TokenSubQueue
        from care.emr.models.scheduling.booking import TokenSlot
        from django.utils import timezone

        w = self.world
        o = w.objects
        now = timezone.now()
        f = f"/api/v1/facility/{w.facility.external_id}"
        booking = w.booking
        slot = booking.token_slot
        later = TokenSlot.objects.create(
            resource=slot.resource,
            availability=slot.availability,
            start_datetime=now + datetime.timedelta(hours=2),
            end_datetime=now + datetime.timedelta(hours=2, minutes=15),
        )
        sub_queue = TokenSubQueue.objects.create(
            facility=w.facility, resource=slot.resource, name="Room 1", status="active"
        )
        account = o["account"]
        draft = Invoice.objects.create(
            facility=w.facility,
            patient=w.patient,
            account=account,
            status="draft",
            title="Interim bill",
        )
        billed = ChargeItem.objects.create(
            facility=w.facility,
            title="Ward bed",
            patient=w.patient,
            encounter=w.encounter,
            account=account,
            status="billed",
            quantity=1,
            unit_price_components=[],
            total_price_components=[],
            total_price=0,
        )
        with_item = Invoice.objects.create(
            facility=w.facility,
            patient=w.patient,
            account=account,
            status="draft",
            title="Bill with an item",
            charge_items=[billed.id],
        )
        billed.paid_invoice = with_item
        billed.save()
        ids = {
            "booking": booking.external_id,
            "later": later.external_id,
            "queue": o["token_queue"].external_id,
            "token": o["token"].external_id,
            "category": str(o["token_category"].external_id),
            "sub_queue": str(sub_queue.external_id),
            "doctor": str(w.users["Doctor"].external_id),
            "patient": str(w.patient.external_id),
            "account": str(account.external_id),
            "draft": draft.external_id,
            "with_item": with_item.external_id,
            "charge": str(o["charge_item"].external_id),
            "billed": str(billed.external_id),
            "payment": o["payment_reconciliation"].external_id,
        }
        today = timezone.localdate().isoformat()
        queue = f"{f}/token/queue/{ids['queue']}"
        token = f"{queue}/token/{ids['token']}"
        new_queue = {
            "name": "Evening queue",
            "resource_type": "practitioner",
            "resource_id": ids["doctor"],
            "date": today,
        }
        account_body = {
            "status": "active",
            "billing_status": "open",
            "name": "IPD account",
            "service_period": {},
        }
        payment = {
            "reconciliation_type": "payment",
            "status": "active",
            "kind": "deposit",
            "issuer_type": "patient",
            "outcome": "complete",
            "method": "cash",
            "tendered_amount": "200.00",
            "returned_amount": "0.00",
        }
        a = f"{f}/appointments/{ids['booking']}"
        cases = (
            # bookings and slots (the Receptionist: can_write_booking, can_reschedule_booking)
            ("put", f"{a}/", {"status": "booked", "note": "moved to the afternoon"}),
            ("patch", f"{a}/", {"status": "booked", "note": "moved to the afternoon"}),
            ("post", f"{a}/cancel/", {"reason": "cancelled"}),
            ("post", f"{a}/reschedule/", {"new_slot": str(ids["later"]), "new_booking_note": "later"}),
            ("post", f"{a}/generate_token/", {"category": ids["category"]}),
            ("post", f"{a}/set_tags/", {"tags": []}),
            ("post", f"{a}/remove_tags/", {"tags": []}),
            ("post", f"{f}/slots/{ids['later']}/create_appointment/", {"patient": ids["patient"], "note": "first visit"}),
            # queues and tokens (can_write_token)
            ("post", f"{f}/token/queue/", new_queue),
            ("post", f"{f}/token/queue/upsert/", {"datapoints": [new_queue]}),
            ("post", f"{f}/token/queue/generate_token/", {**new_queue, "category": ids["category"], "patient": ids["patient"]}),
            ("put", f"{queue}/", {"name": "OPD queue, room 2"}),
            ("patch", f"{queue}/", {"name": "OPD queue, room 2"}),
            ("post", f"{queue}/set_primary/", {}),
            ("post", f"{queue}/set_next_token_to_subqueue/", {"sub_queue": ids["sub_queue"]}),
            ("post", f"{queue}/token/", {"category": ids["category"], "patient": ids["patient"]}),
            ("post", f"{queue}/token/upsert/", {"datapoints": [{"category": ids["category"]}]}),
            ("put", f"{token}/", {"status": "CREATED", "note": "at the desk", "sub_queue": None}),
            ("patch", f"{token}/", {"status": "CREATED", "note": "at the desk", "sub_queue": None}),
            ("post", f"{token}/set_next/", {"sub_queue": ids["sub_queue"]}),
            ("delete", f"{token}/", None, 204),
            ("delete", f"{queue}/", None, 204),
            # accounts (can_create_account; can_update_account)
            ("post", f"{f}/account/", {**account_body, "name": "OPD account", "patient": str(o["patient_b"].external_id)}),
            ("post", f"{f}/account/default_account/", {"patient": ids["patient"], "facility": str(w.facility.external_id), "encounter": str(w.encounter.external_id)}),
            ("put", f"{f}/account/{ids['account']}/", account_body),
            ("patch", f"{f}/account/{ids['account']}/", account_body),
            ("post", f"{f}/account/{ids['account']}/rebalance/", {}),
            ("post", f"{f}/account/{ids['account']}/set_tags/", {"tags": []}),
            ("post", f"{f}/account/{ids['account']}/remove_tags/", {"tags": []}),
            # invoices (can_write_invoice)
            ("post", f"{f}/invoice/", {"status": "draft", "account": ids["account"], "title": "Second bill"}),
            ("put", f"{f}/invoice/{ids['draft']}/", {"status": "draft", "title": "Interim bill, revised"}),
            ("patch", f"{f}/invoice/{ids['draft']}/", {"status": "draft", "title": "Interim bill, revised"}),
            ("post", f"{f}/invoice/{ids['draft']}/attach_account_to_invoice/", {}),
            ("post", f"{f}/invoice/{ids['draft']}/attach_items_to_invoice/", {"charge_items": [ids["charge"]]}),
            ("post", f"{f}/invoice/{ids['with_item']}/remove_item_from_invoice/", {"charge_item": ids["billed"]}),
            # payments (can_write_payment_reconciliation)
            ("post", f"{f}/payment_reconciliation/", {**payment, "account": ids["account"]}),
            ("post", f"{f}/payment_reconciliation/change_account/", {"payment_reconciliations": [str(ids["payment"])], "target_account": ids["account"]}, 201),
            ("put", f"{f}/payment_reconciliation/{ids['payment']}/", {**payment, "note": "receipt 12"}),
            ("patch", f"{f}/payment_reconciliation/{ids['payment']}/", {**payment, "note": "receipt 12"}),
        )  # fmt: skip
        for method, url, body, *code in cases:
            self.write(method, url, body, *code)
        self.assert_every_cell_sent(DESK_TEST)


class StoreRows(MatrixCase):
    """Review M8: CARE's delivery-order list (it needs ?request_order) builds its orders from
    the deliveries the viewset's queryset gives, so the patient's return reaches the Store Keeper
    only if the patient-row filter fails (Q-K43 A)."""

    def test_delivery_orders_of_a_request_order(self):
        o = self.world.objects
        url = (
            "/api/v1/supply_delivery/delivery_orders/"
            f"?request_order={o['request_order'].external_id}"
        )
        stock, returned = (
            str(o[k].external_id) for k in ("delivery_order", "return_order")
        )
        seen = {}
        for who in ("Store Keeper", "Facility Admin"):
            answer = self.api(who).get(url)
            self.assertEqual(answer.status_code, 200, answer.content[:300])
            seen[who] = sorted(r["id"] for r in answer.json()["results"])
        self.assertEqual(seen["Store Keeper"], [stock])
        self.assertEqual(seen["Facility Admin"], sorted([stock, returned]))


class SpecWalk(SimpleTestCase):
    """§10 item 16: every CARE (or our) spec whose model is Patient or Encounter carries the
    guard, or is listed in spec_exemptions.txt with why; a write or base spec listed there is
    never any viewset's read model and never serialized in CARE's or our code."""

    EXEMPTIONS = matrix.HERE / "spec_exemptions.txt"

    def specs(self):
        from care.emr.models import Encounter, Patient
        from care.emr.resources.base import EMRResource

        import ihr_access.edit  # noqa: F401  (our own write spec)

        found, todo = set(), [EMRResource]
        while todo:
            for sub in todo.pop().__subclasses__():
                if sub not in found:
                    found.add(sub)
                    todo.append(sub)
        return {
            f"{c.__module__}.{c.__qualname__}": c
            for c in found
            if getattr(c, "__model__", None) in (Patient, Encounter)
        }

    def test_every_patient_and_encounter_spec_is_guarded_or_exempt(self):
        exempt = guard.read_list(
            self.EXEMPTIONS, verbs=frozenset({"write", "base", "exempt"})
        )
        specs = self.specs()
        self.assertEqual(sorted(set(exempt) - set(specs)), [], "stale exemptions")
        read_models = set()
        for view in self.views():
            read_models |= self.read_models_of(view)
        sources = self._sources()
        for name, cls in sorted(specs.items()):
            with self.subTest(spec=name):
                if name not in exempt:
                    self.assertIn(
                        getattr(cls.serialize, guard.MARKER, None),
                        ("patient", "encounter"),
                        "not guarded and not in spec_exemptions.txt",
                    )
                    continue
                verb, _reason = exempt[name]
                if verb in ("write", "base"):
                    self.assertNotIn(cls, read_models, "a viewset reads with it")
                    pattern = re.compile(rf"\b{cls.__name__}\.serialize\(")
                    self.assertEqual(
                        [path for path, text in sources if pattern.search(text)],
                        [],
                        "serialized somewhere",
                    )

    @staticmethod
    def views():
        """Every view class of the hospital's URL configuration (all URL confs, our swaps and
        the OTP routers included) and every class registered on a router of config.api_router."""
        from config import api_router
        from rest_framework.routers import BaseRouter

        found = {cls for _r, cls, _m in matrix.get_route_views() if cls is not None}
        for router in vars(api_router).values():
            if isinstance(router, BaseRouter):
                found |= {viewset for _p, viewset, _b in router.registry}
        return found

    @staticmethod
    def read_models_of(view):
        """What a view serializes its reads with, by CARE's own fallback (review M7/O2):
        ``get_retrieve_pydantic_model`` -> ``get_read_pydantic_model`` -> ``pydantic_model``."""
        from care.emr.api.viewsets.base import EMRBaseViewSet

        found = set()
        if isinstance(view, type) and issubclass(view, EMRBaseViewSet):
            instance = view.__new__(view)
            for getter in ("get_read_pydantic_model", "get_retrieve_pydantic_model"):
                try:
                    model = getattr(instance, getter)()
                except Exception:
                    model = None
                if model is not None:
                    found.add(model)
        read = getattr(view, "pydantic_read_model", None) or getattr(
            view, "pydantic_model", None
        )
        retrieve = getattr(view, "pydantic_retrieve_model", None) or read
        return found | {m for m in (read, retrieve) if m is not None}

    def test_a_view_reading_with_a_write_spec_is_caught(self):
        """The fallback case: a viewset with only ``pydantic_model = PatientCreateSpec`` reads
        with it, and the walk sees that."""
        from care.emr.api.viewsets.base import EMRBaseViewSet, EMRListMixin
        from care.emr.resources.patient.spec import PatientCreateSpec

        class Fallback(EMRListMixin, EMRBaseViewSet):
            pydantic_model = PatientCreateSpec

        self.assertIn(PatientCreateSpec, self.read_models_of(Fallback))
        exempt = guard.read_list(
            self.EXEMPTIONS, verbs=frozenset({"write", "base", "exempt"})
        )
        name = f"{PatientCreateSpec.__module__}.{PatientCreateSpec.__qualname__}"
        self.assertEqual(exempt[name][0], "write")

    @staticmethod
    def _sources():
        from pathlib import Path

        import care

        import ihr_access

        roots = [Path(care.__file__).parent, Path(ihr_access.__file__).parent.parent]
        out = []
        for root in roots:
            for path in root.rglob("*.py"):
                if "tests" in path.parts or "migrations" in path.parts:
                    continue
                out.append((str(path), path.read_text(errors="replace")))
        return out


# GET routes on batch_allowlist.txt -> the batch case that proves a guarded answer inside a
# batch (§10 item 20's meta-test: a newly listed GET fails until it has one)
BATCH_GET_CASES = {
    "/api/v1/medication/dispense/{id}/": "test_cashier_dispense_get_inside_a_batch",
}


class BatchCases(MatrixCase):
    """§10 item 20: batch sub-requests reach our swaps and the guard. Each case checks that
    the batch itself was not refused (no ``ihr-batch: refused`` line)."""

    def batch(self, who, *requests):
        from unittest import mock

        from ihr_platform import batch

        with (
            identity.fake_keycloak(),
            mock.patch.object(batch, "_emit") as emitted,
        ):
            client = self.signed_in(who)
            response = client.post(
                "/api/v1/batch_requests/",
                {
                    "requests": [
                        {
                            "url": url,
                            "method": method,
                            "body": body,
                            "reference_id": str(i),
                        }
                        for i, (method, url, body) in enumerate(requests)
                    ]
                },
                format="json",
            )
        return response, emitted

    def assert_ran(self, response, emitted):
        emitted.assert_not_called()
        for result in response.json()["results"]:
            self.assertNotIn("not allowed in a batch", json_text(result))

    def test_receptionist_patient_put_keeps_the_blood_group(self):
        from ihr_access.tests.base import registration_body

        w = self.world
        url = f"/api/v1/patient/{w.patient.external_id}/"
        got = self.api("Receptionist").get(url).json()
        body = registration_body(
            got, blood_group="O_negative", address="7 Batch Lane, Pune"
        )
        response, emitted = self.batch("Receptionist", ("PUT", url, body))
        self.assert_ran(response, emitted)
        [result] = response.json()["results"]
        self.assertEqual(result["status_code"], 200, result)
        self.assertNotIn(testing.CANARY, json_text(result))
        w.patient.refresh_from_db()
        self.assertEqual(w.patient.blood_group, "AB_negative")
        self.assertEqual(w.patient.address, "7 Batch Lane, Pune")

    def test_front_desk_encounter_put_refused_inside_a_batch(self):
        w = self.world
        url = f"/api/v1/encounter/{w.encounter.external_id}/"
        body = {
            "status": "completed",
            "encounter_class": "imp",
            "priority": "routine",
            "period": {},
            "hospitalization": {},
            "discharge_summary_advice": "changed by the front desk",
        }
        for role in ("Receptionist", "Cashier"):
            with self.subTest(role=role):
                response, emitted = self.batch(role, ("PUT", url, body))
                self.assert_ran(response, emitted)
                [result] = response.json()["results"]
                self.assertIn(result["status_code"], (403, 404), result)
                self.assertNotIn(testing.CANARY, json_text(result))
        w.encounter.refresh_from_db()
        self.assertEqual(w.encounter.status, "in_progress")

    def test_cashier_dispense_get_inside_a_batch(self):
        dispense = self.world.objects["medication_dispense"]
        url = f"/api/v1/medication/dispense/{dispense.external_id}/"
        response, emitted = self.batch("Cashier", ("GET", url, {}))
        self.assert_ran(response, emitted)
        [result] = response.json()["results"]
        text = json_text(result)
        self.assertIn(result["status_code"], (403, 404), result)
        self.assertNotIn(testing.CANARY, text)
        self.assertNotIn(self.world.patient.name, text)

    def test_a_get_off_the_list_is_refused_labelled(self):
        """The labelled case: a GET not on batch_allowlist.txt refuses the whole batch."""
        url = f"/api/v1/encounter/{self.world.encounter.external_id}/"
        response, emitted = self.batch("Receptionist", ("GET", url, {}))
        self.assertEqual(response.status_code, 400)
        emitted.assert_called_once()
        self.assertIn("reason=not_listed", emitted.call_args.args[0])
        self.assertNotIn(self.world.patient.name, response.content.decode())

    def test_every_listed_get_has_a_batch_case(self):
        from ihr_platform import batch

        listed = {t for method, t in batch.load_allow_list() if method == "GET"}
        self.assertEqual(
            sorted(listed ^ set(BATCH_GET_CASES)),
            [],
            "batch_allowlist.txt GET lines differ from BATCH_GET_CASES: add a BatchCases case "
            "for a new GET (or drop the entry of a removed one); docs/UPSTREAM.md, batch step",
        )
        for name in BATCH_GET_CASES.values():
            self.assertTrue(callable(getattr(self, name, None)), name)


def json_text(value):
    import json

    return json.dumps(value, default=str)
