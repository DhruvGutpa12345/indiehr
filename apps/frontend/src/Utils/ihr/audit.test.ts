// IndieHR P1-T24a (docs/reviews/p1-t24/design.md §2.1, attack.md "Decision"): the audit trail page's
// rules. Run: node --import tsx --test src/Utils/ihr/audit.test.ts
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  type AuditEntry,
  type AuditStatus,
  actorParts,
  auditFilterKey,
  auditUrlParams,
  buildAuditQuery,
  checkAuditRange,
  fingerprintState,
  patientsShown,
  readAuditUrl,
  resultOf,
  statusStrip,
  toApiDay,
  valueRows,
  whatOf,
  withDraftDates,
} from "./audit";

// 21-11-2026 12:00 IST
const NOON = Date.UTC(2026, 10, 21, 6, 30);

const entry = (over: Partial<AuditEntry> = {}): AuditEntry => ({
  id: 7,
  at: "2026-11-21T06:30:00+00:00",
  at_ist: "21-11-2026 12:00:00",
  kind: "write",
  actor: "user:12:priya.admin",
  purpose: null,
  grants: null,
  via: "web",
  ip: "10.0.0.1",
  method: "PATCH",
  path: "/api/v1/patient/abc/",
  query: null,
  task: null,
  request_id: "r1",
  device_tz: null,
  table: "emr_patient",
  op: "UPDATE",
  record: "5",
  patients: [{ id: "00000000-0000-4000-8000-000000000001", name: "Asha" }],
  patients_total: 1,
  fields: ["address", "phone_number"],
  ...over,
});

describe("toApiDay", () => {
  it("turns DD-MM-YYYY into the API's YYYY-MM-DD", () => {
    assert.equal(toApiDay("21-11-2026"), "2026-11-21");
    assert.equal(toApiDay("1-2-2026"), "2026-02-01");
    assert.equal(toApiDay(" 05-01-2027 "), "2027-01-05");
  });
  it("refuses what is not a real day", () => {
    for (const bad of [
      "",
      "2026-11-21",
      "31-02-2026",
      "32-01-2026",
      "x",
      "1-1-26",
    ]) {
      assert.equal(toApiDay(bad), null, bad);
    }
  });
});

describe("readAuditUrl: today in IST by default (Q-K114 A)", () => {
  it("defaults From and To to today, IST", () => {
    const f = readAuditUrl({}, NOON);
    assert.equal(f.from, "21-11-2026");
    assert.equal(f.to, "21-11-2026");
    assert.deepEqual([f.kind, f.op, f.patient, f.actor], ["", "", "", ""]);
  });

  it("uses the IST day at 23:59 IST and 00:01 IST, not the UTC day", () => {
    // 21-11-2026 23:59 IST = 18:29 UTC the same day
    assert.equal(
      readAuditUrl({}, Date.UTC(2026, 10, 21, 18, 29)).from,
      "21-11-2026",
    );
    // 22-11-2026 00:01 IST = 18:31 UTC on the 21st
    assert.equal(
      readAuditUrl({}, Date.UTC(2026, 10, 21, 18, 31)).from,
      "22-11-2026",
    );
  });

  it("keeps what the address holds and drops odd values", () => {
    const f = readAuditUrl(
      {
        from: "01-11-2026",
        to: "10-11-2026",
        kind: "request",
        op: "UPDATE",
        patient: "00000000-0000-4000-8000-000000000001",
        actor: "user:12:priya.admin",
      },
      NOON,
    );
    assert.deepEqual(f, {
      from: "01-11-2026",
      to: "10-11-2026",
      kind: "request",
      op: "UPDATE",
      patient: "00000000-0000-4000-8000-000000000001",
      actor: "user:12:priya.admin",
    });
    const odd = readAuditUrl(
      { kind: "nope", op: "drop", patient: "not-a-uuid", actor: 5, from: 3 },
      NOON,
    );
    assert.deepEqual(
      [odd.kind, odd.op, odd.patient, odd.actor],
      ["", "", "", ""],
    );
    assert.equal(odd.from, "21-11-2026");
  });

  it("cuts an actor at 200 characters, as the backend does", () => {
    assert.equal(
      readAuditUrl({ actor: "a".repeat(300) }, NOON).actor.length,
      200,
    );
  });
});

describe("checkAuditRange", () => {
  it("returns the API days for a good range", () => {
    assert.deepEqual(checkAuditRange("01-11-2026", "21-11-2026"), {
      errors: {},
      range: { from: "2026-11-01", to: "2026-11-21" },
    });
    assert.deepEqual(checkAuditRange("21-11-2026", "21-11-2026").range, {
      from: "2026-11-21",
      to: "2026-11-21",
    });
  });
  it("names a mistake with a string key and sends no range", () => {
    const bad = checkAuditRange("xx", "21-11-2026");
    assert.equal(bad.range, null);
    assert.equal(bad.errors.from, "ihr_totals_error_format");
    assert.equal(
      checkAuditRange("21-11-2026", "yy").errors.to,
      "ihr_totals_error_format",
    );
    assert.equal(
      checkAuditRange("22-11-2026", "21-11-2026").errors.to,
      "ihr_totals_error_order",
    );
    assert.equal(
      checkAuditRange("31-12-1999", "21-11-2026").errors.from,
      "ihr_totals_error_before_min",
    );
  });
});

describe("buildAuditQuery and the address", () => {
  const f = readAuditUrl(
    {
      kind: "write",
      op: "UPDATE",
      patient: "00000000-0000-4000-8000-000000000001",
    },
    NOON,
  );
  const range = { from: "2026-11-21", to: "2026-11-21" };

  it("sends only the filters that are set, with the API's date format", () => {
    assert.deepEqual(buildAuditQuery(f, range), {
      from: "2026-11-21",
      to: "2026-11-21",
      kind: "write",
      op: "UPDATE",
      patient: "00000000-0000-4000-8000-000000000001",
    });
    assert.deepEqual(buildAuditQuery(readAuditUrl({}, NOON), range), range);
  });

  it("adds the older-entries cursor only when there is one", () => {
    assert.equal(buildAuditQuery(f, range, 41).before, "41");
    assert.equal("before" in buildAuditQuery(f, range), false);
    assert.equal("before" in buildAuditQuery(f, range, null), false);
  });

  it("sends an actor link as an exact actor (actor_exact), never as a prefix", () => {
    const q = buildAuditQuery(
      readAuditUrl({ actor: "user:12:priya.admin" }, NOON),
      range,
    );
    assert.equal(q.actor_exact, "user:12:priya.admin");
    assert.equal("actor" in q, false);
  });

  it("writes the address with no empty keys, and the key changes with any filter", () => {
    assert.deepEqual(auditUrlParams(readAuditUrl({}, NOON)), {
      from: "21-11-2026",
      to: "21-11-2026",
    });
    assert.deepEqual(auditUrlParams(f), {
      from: "21-11-2026",
      to: "21-11-2026",
      kind: "write",
      op: "UPDATE",
      patient: "00000000-0000-4000-8000-000000000001",
    });
    const keys = new Set([
      auditFilterKey(f),
      auditFilterKey({ ...f, from: "20-11-2026" }),
      auditFilterKey({ ...f, to: "22-11-2026" }),
      auditFilterKey({ ...f, kind: "request" }),
      auditFilterKey({ ...f, op: "" }),
      auditFilterKey({ ...f, patient: "" }),
      auditFilterKey({ ...f, actor: "user:1:x" }),
    ]);
    assert.equal(keys.size, 7);
  });

  it('"Show all" brings back the first load\'s key, so its cached list is reused (review W1)', () => {
    // The first load has no address keys; an actor link writes from, to and actor; "Show all"
    // writes from and to again. Both read back as the same filters, so the same query key:
    // a list fetched under a minute ago is served from the cache with no new request.
    const first = readAuditUrl({}, NOON);
    const byActor = readAuditUrl(
      auditUrlParams({ ...first, actor: "user:12:priya.admin" }),
      NOON,
    );
    const shownAll = readAuditUrl(
      auditUrlParams({ ...byActor, actor: "" }),
      NOON,
    );
    assert.notEqual(auditFilterKey(byActor), auditFilterKey(first));
    assert.equal(auditFilterKey(shownAll), auditFilterKey(first));
  });
});

describe("actorParts: the actor is user:<pk>:<username>", () => {
  it("shows the username and keeps the whole actor for the exact filter", () => {
    assert.deepEqual(actorParts("user:12:priya.admin"), {
      kind: "user",
      label: "priya.admin",
      actor: "user:12:priya.admin",
    });
    assert.equal(actorParts("service:3:provisioner").kind, "service");
    assert.equal(actorParts("service:3:provisioner").label, "provisioner");
  });
  it("keeps a username that holds colons whole", () => {
    assert.equal(actorParts("user:4:a:b").label, "a:b");
  });
  it("shows other actors as they are", () => {
    for (const other of [
      "anonymous",
      "patient:***1234",
      "system:celery",
      "x",
    ]) {
      const p = actorParts(other);
      assert.equal(p.label, other);
      assert.equal(p.actor, other);
    }
    assert.equal(actorParts("patient:***1234").kind, "patient");
    assert.equal(actorParts("system:celery").kind, "system");
    assert.equal(actorParts("anonymous").kind, "anonymous");
    assert.equal(actorParts("").kind, "other");
  });
});

describe("whatOf, resultOf, patientsShown", () => {
  it("describes a request by purpose and path, a change by table and op", () => {
    assert.deepEqual(
      whatOf(
        entry({
          kind: "request",
          purpose: "audit",
          path: "/api/ihr/audit/status/",
          op: null,
          table: null,
        }),
      ),
      {
        type: "request",
        purpose: "audit",
        method: "PATCH",
        path: "/api/ihr/audit/status/",
      },
    );
    assert.deepEqual(whatOf(entry()), {
      type: "change",
      table: "emr_patient",
      op: "UPDATE",
    });
    assert.deepEqual(
      whatOf(entry({ kind: "truncate", op: "TRUNCATE", table: "emr_x" })),
      {
        type: "change",
        table: "emr_x",
        op: "TRUNCATE",
      },
    );
  });

  it("gives the status of a request, the changed fields of an update, else nothing", () => {
    assert.deepEqual(resultOf(entry({ kind: "request", status: 403 })), {
      type: "status",
      status: 403,
    });
    assert.deepEqual(resultOf(entry({ kind: "request", status: undefined })), {
      type: "none",
    });
    assert.deepEqual(resultOf(entry()), {
      type: "fields",
      fields: ["address", "phone_number"],
    });
    assert.deepEqual(resultOf(entry({ op: "INSERT", fields: undefined })), {
      type: "none",
    });
  });

  it("counts the patients not named in the list", () => {
    assert.deepEqual(patientsShown(entry()), {
      shown: [{ id: "00000000-0000-4000-8000-000000000001", name: "Asha" }],
      more: 0,
    });
    assert.equal(patientsShown(entry({ patients_total: 25 })).more, 24);
    assert.equal(
      patientsShown(entry({ patients: [], patients_total: 0 })).more,
      0,
    );
    // never negative
    assert.equal(patientsShown(entry({ patients_total: 0 })).more, 0);
  });

  it("tolerates a patient the lookup could not name", () => {
    const p = patientsShown(
      entry({ patients: [{ id: null, name: null }], patients_total: 1 }),
    );
    assert.deepEqual(p.shown, [{ id: null, name: null }]);
  });
});

describe("valueRows: old and new values of one entry", () => {
  it("shows no values for a request entry (its data is the result, not a record)", () => {
    assert.deepEqual(
      valueRows(null, { status: 200, patients_total: 3 }, "request"),
      [],
    );
    assert.deepEqual(valueRows("INSERT", { a: 1 }, "write"), [
      { field: "a", value: "1" },
    ]);
  });

  it("pairs old and new for an update, in field order", () => {
    assert.deepEqual(
      valueRows("UPDATE", {
        phone_number: ["111", "222"],
        address: [null, "Pune"],
      }),
      [
        { field: "address", old: "-", new: "Pune" },
        { field: "phone_number", old: "111", new: "222" },
      ],
    );
  });
  it("shows a hidden secret as the backend marks it, once", () => {
    assert.deepEqual(valueRows("UPDATE", { password: "(hidden)" }), [
      { field: "password", old: "(hidden)", new: "(hidden)" },
    ]);
  });
  it("shows one value for an insert or a delete, and objects as JSON", () => {
    assert.deepEqual(
      valueRows("INSERT", { id: 5, meta: { a: 1 }, gone: null }),
      [
        { field: "gone", value: "-" },
        { field: "id", value: "5" },
        { field: "meta", value: '{"a":1}' },
      ],
    );
    assert.deepEqual(valueRows("DELETE", { x: true }), [
      { field: "x", value: "true" },
    ]);
  });
  it("is empty for no values", () => {
    for (const v of [null, undefined, [], "x", 5]) {
      assert.deepEqual(valueRows("UPDATE", v), []);
    }
  });
});

describe("statusStrip: the line above the list", () => {
  const status = (over: Partial<AuditStatus> = {}): AuditStatus => ({
    seals: 4,
    head: "ab",
    last_sealed_at: "21-11-2026 10:42:00",
    unsealed_entries: 3,
    oldest_unsealed_at: "21-11-2026 10:43:10",
    triggers_ok: true,
    trigger_problems: [],
    fingerprints: { configured: true, accepted: true, readable: undefined },
    ...over,
  });

  it("reports what the answer says", () => {
    assert.deepEqual(statusStrip(status()), {
      lastSealed: "21-11-2026 10:42:00",
      unsealed: 3,
      triggersOk: true,
      triggerProblems: 0,
      fingerprints: "locked",
    });
  });
  it("counts trigger problems and never says ok when there are some", () => {
    const s = statusStrip(
      status({ triggers_ok: false, trigger_problems: ["a", "b"] }),
    );
    assert.equal(s.triggersOk, false);
    assert.equal(s.triggerProblems, 2);
    // triggers_ok true but a list left over: not ok (fail closed)
    assert.equal(
      statusStrip(status({ trigger_problems: ["a"] })).triggersOk,
      false,
    );
  });
  it("has no last-sealed time before the first seal", () => {
    assert.equal(
      statusStrip(status({ seals: 0, last_sealed_at: null })).lastSealed,
      null,
    );
  });
  it("never invents a figure the answer lacks", () => {
    const s = statusStrip({} as AuditStatus);
    assert.equal(s.lastSealed, null);
    assert.equal(s.unsealed, null);
    assert.equal(s.triggersOk, false);
    assert.equal(s.fingerprints, "unknown");
  });
});

describe("fingerprintState", () => {
  it("maps the outside bucket's answer to one word", () => {
    assert.equal(fingerprintState({ configured: false }), "not_configured");
    assert.equal(
      fingerprintState({ configured: true, readable: false }),
      "unreadable",
    );
    assert.equal(
      fingerprintState({ configured: true, accepted: false }),
      "problem",
    );
    assert.equal(
      fingerprintState({ configured: true, accepted: true }),
      "locked",
    );
    assert.equal(fingerprintState(undefined), "unknown");
    assert.equal(fingerprintState(null), "unknown");
    assert.equal(fingerprintState({ configured: true }), "unknown");
  });
});

describe("withDraftDates: a filter changed while dates are typed", () => {
  const base = readAuditUrl({}, NOON);

  it("sends the typed dates with the change instead of dropping them", () => {
    const { errors, next } = withDraftDates(base, "01-11-2026", "15-11-2026", {
      kind: "write",
    });
    assert.deepEqual(errors, {});
    assert.equal(next?.from, "01-11-2026");
    assert.equal(next?.to, "15-11-2026");
    assert.equal(next?.kind, "write");
  });

  it("makes no change, and says why, when a typed date is mistaken", () => {
    const { errors, next } = withDraftDates(base, "32-13-2026", base.to, {
      op: "UPDATE",
    });
    assert.equal(next, null);
    assert.ok(errors.from);
  });
});
