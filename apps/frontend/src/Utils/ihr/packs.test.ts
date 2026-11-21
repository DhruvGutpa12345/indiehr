// IndieHR P1-T24c (docs/reviews/p1-t24/design.md "T24c as decided"): the pack updates page's
// rules. Run: node --import tsx --test src/Utils/ihr/packs.test.ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

import {
  MAX_REASON,
  PAGE_SIZE,
  answerOf,
  canAccept,
  canKeep,
  canRetryLoad,
  checkLine,
  clampOffset,
  decisionBody,
  effectKey,
  facilityText,
  fieldValue,
  historyBy,
  historySummary,
  loadErrorKey,
  pageRange,
  readTab,
  reasonCheck,
  sinceText,
  whyKey,
  type PackDetail,
  type PackHistoryEntry,
} from "./packs";

const detail = (over: Partial<PackDetail> = {}): PackDetail => ({
  id: 7,
  pack: "care-valuesets",
  kind: "valueset",
  item: "system-additional-instruction",
  label: "Additional instruction",
  facility: null,
  facility_name: null,
  state: "pending",
  reason: "unknown-origin",
  applied_version: 1,
  pending_version: 2,
  changed_at: "2026-11-21T10:00:00+05:30",
  pending_sha256: "a".repeat(64),
  row_sha256: "b".repeat(64),
  effect: "update",
  blocked: "",
  conflicts: {},
  fields: [],
  ...over,
});

describe("effect and reason words", () => {
  it("maps every effect the backend sends, and anything else to the generic one", () => {
    for (const effect of [
      "update",
      "create",
      "restore",
      "retire",
      "new-version",
      "status-only",
    ]) {
      assert.equal(effectKey(effect), `ihr_packs_effect__${effect}`);
    }
    assert.equal(effectKey("something-new"), "ihr_packs_effect__other");
    assert.equal(effectKey(""), "ihr_packs_effect__other");
  });

  it("maps every hold reason, and shows nothing for none", () => {
    for (const reason of [
      "edited",
      "unknown-origin",
      "deleted",
      "conflict",
      "data-check",
      "privacy",
      "counter-restart",
      "missing-link",
    ]) {
      assert.equal(whyKey(reason), `ihr_packs_why__${reason}`);
    }
    assert.equal(whyKey(""), null);
    assert.equal(whyKey("new-reason"), "ihr_packs_why__other");
  });
});

describe("facility name", () => {
  it("is the name, or 'whole hospital' for a hospital-wide item", () => {
    assert.equal(
      facilityText({ facility: "x", facility_name: "Alpha Clinic" }, "Whole"),
      "Alpha Clinic",
    );
    assert.equal(
      facilityText({ facility: null, facility_name: null }, "Whole"),
      "Whole",
    );
    // a facility with no name given (an older answer) is never shown as the id
    assert.equal(
      facilityText({ facility: "uuid-1", facility_name: null }, "Whole"),
      "-",
    );
  });
});

describe("keep and accept", () => {
  it("keeps only a pending item; accepts unless blocked", () => {
    assert.equal(canKeep(detail({ state: "pending" })), true);
    assert.equal(canKeep(detail({ state: "kept" })), false);
    assert.equal(canAccept(detail()), true);
    assert.equal(canAccept(detail({ blocked: "conflict" })), false);
    // a kept item is asked afresh at its accept: never blocked in the answer
    assert.equal(canAccept(detail({ state: "kept", blocked: "" })), true);
  });

  it("builds the body the backend checks: what was shown, and the reason", () => {
    assert.deepEqual(decisionBody(detail(), "keep", "  a   ticket "), {
      action: "keep",
      version: 2,
      sha256: "a".repeat(64),
      row_sha256: "b".repeat(64),
      reason: "a ticket",
    });
  });
});

describe("the reason", () => {
  it("is required, one line, and at most the backend's limit", () => {
    assert.equal(reasonCheck("").ok, false);
    assert.equal(reasonCheck("   \n ").ok, false);
    assert.equal(reasonCheck("ticket 12").ok, true);
    assert.equal(reasonCheck("x".repeat(MAX_REASON)).ok, true);
    assert.equal(reasonCheck("x".repeat(MAX_REASON + 1)).ok, false);
    assert.equal(MAX_REASON, 300);
    // many spaces count as one, as the backend counts them
    assert.equal(reasonCheck(`a${" ".repeat(400)}b`).ok, true);
    assert.equal(reasonCheck("a  b").text, "a b");
  });
});

describe("every POST answer is a fixed phrase (the status table)", () => {
  const cases: [string, number, unknown, string, string][] = [
    // row, status, body, key, what the page does next
    ["1", 200, { unchanged: false }, "ihr_packs_done_keep", "close"],
    ["2", 200, { unchanged: true }, "ihr_packs_done_already", "close"],
    [
      "3a",
      400,
      { reason: "a reason is required" },
      "ihr_packs_error_reason",
      "stay",
    ],
    ["3b", 400, { action: "accept or keep" }, "ihr_packs_error_save", "stay"],
    [
      "3c",
      400,
      { detail: "Send a JSON object." },
      "ihr_packs_error_save",
      "stay",
    ],
    [
      "4",
      400,
      { reason: "text of the engine", code: "refused" },
      "ihr_packs_error_save",
      "stay",
    ],
    ["5a", 401, { detail: "x" }, "ihr_packs_error_signed_out", "stay"],
    // our session classes send no WWW-Authenticate, so DRF's NotAuthenticated is a 403 (review P1)
    [
      "5a2",
      403,
      { detail: "Authentication credentials were not provided." },
      "ihr_packs_error_signed_out",
      "stay",
    ],
    ["5b", 403, { detail: "x" }, "ihr_packs_error_forbidden", "stay"],
    [
      "6",
      404,
      { detail: "x", code: "not-found" },
      "ihr_packs_error_gone",
      "list",
    ],
    [
      "7",
      409,
      { detail: "x", code: "not-live" },
      "ihr_packs_error_not_live",
      "stay",
    ],
    [
      "8",
      409,
      { detail: "x", code: "stale", stale: true },
      "ihr_packs_error_stale",
      "detail",
    ],
    [
      "9",
      409,
      { detail: "x", code: "blocked", blocked: true },
      "ihr_packs_error_blocked",
      "detail",
    ],
    [
      "10",
      409,
      { detail: "x", code: "busy", busy: true },
      "ihr_packs_error_busy",
      "stay",
    ],
    ["11", 429, { detail: "x", code: "rate" }, "ihr_packs_error_many", "stay"],
    [
      "12",
      429,
      { detail: "x", code: "in-flight" },
      "ihr_packs_error_many",
      "stay",
    ],
    [
      "13",
      503,
      { detail: "x", code: "unavailable" },
      "ihr_packs_error_unavailable",
      "stay",
    ],
    [
      "14",
      503,
      { detail: "upgrading", code: "ihr_upgrading" },
      "ihr_packs_error_upgrading",
      "stay",
    ],
  ];
  for (const [row, status, body, key, after] of cases) {
    it(`row ${row}: ${status} ${JSON.stringify(body)}`, () => {
      const out = answerOf("keep", status, body);
      assert.equal(out.key, key);
      assert.equal(out.after, after);
    });
  }

  it("says accepted after an accept, kept after a keep", () => {
    assert.equal(
      answerOf("accept", 200, { unchanged: false }).key,
      "ihr_packs_done_accept",
    );
    assert.equal(
      answerOf("keep", 200, { unchanged: false }).key,
      "ihr_packs_done_keep",
    );
  });

  it("is never the server's own text, and unknown answers are the generic failure", () => {
    for (const body of [
      { detail: "secret operator words", code: "something-new" },
      undefined,
      null,
      "text",
      42,
    ]) {
      for (const status of [0, 400, 409, 418, 500, 502]) {
        const out = answerOf("accept", status, body);
        assert.equal(out.key, "ihr_packs_error_save", `${status} ${body}`);
        assert.equal(out.after, "stay");
      }
    }
    // an older backend: the flags alone still decide a 409
    assert.equal(
      answerOf("accept", 409, { stale: true }).key,
      "ihr_packs_error_stale",
    );
    assert.equal(
      answerOf("accept", 409, { busy: true }).key,
      "ihr_packs_error_busy",
    );
    assert.equal(
      answerOf("accept", 409, { blocked: true }).key,
      "ihr_packs_error_blocked",
    );
    // a 404 without a code is still "no longer waiting"
    assert.equal(answerOf("keep", 404, undefined).key, "ihr_packs_error_gone");
  });
});

describe("load errors", () => {
  it("names forbidden and unavailable; the rest is a plain failure", () => {
    assert.equal(loadErrorKey(403, undefined), "ihr_packs_error_forbidden");
    assert.equal(
      loadErrorKey(503, { code: "unavailable" }),
      "ihr_packs_error_unavailable",
    );
    assert.equal(loadErrorKey(500, undefined), "ihr_packs_error_load");
    assert.equal(loadErrorKey(undefined, undefined), "ihr_packs_error_load");
  });

  it("a signed-out 403 (DRF's own text) is 'signed out', not 'not for your role'", () => {
    assert.equal(
      loadErrorKey(403, {
        detail: "Authentication credentials were not provided.",
      }),
      "ihr_packs_error_signed_out",
    );
    assert.equal(
      loadErrorKey(403, { detail: "You do not have permission." }),
      "ihr_packs_error_forbidden",
    );
  });

  it("a release or restore hold (503 ihr_upgrading) is 'try later'", () => {
    assert.equal(
      loadErrorKey(503, { detail: "upgrading", code: "ihr_upgrading" }),
      "ihr_packs_error_upgrading",
    );
  });

  it("offers a retry only where one can work", () => {
    assert.equal(canRetryLoad(401), false);
    assert.equal(canRetryLoad(403), false);
    assert.equal(canRetryLoad(500), true);
    assert.equal(canRetryLoad(503), true);
    assert.equal(canRetryLoad(undefined), true);
  });
});

describe("values and checks", () => {
  it("shows a field value as text", () => {
    assert.equal(fieldValue(null), "-");
    assert.equal(fieldValue(undefined), "-");
    assert.equal(fieldValue("abc"), "abc");
    assert.equal(fieldValue(3), "3");
    assert.equal(fieldValue(false), "false");
    assert.equal(fieldValue({ a: 1 }), '{"a":1}');
  });

  it("turns the stored data check into fixed phrases", () => {
    assert.equal(checkLine({}), null);
    assert.deepEqual(checkLine({ patients: 3, complete: true }), {
      key: "ihr_packs_check_patients",
      params: { count: 3 },
    });
    assert.deepEqual(checkLine({ patients: 0, complete: true }), {
      key: "ihr_packs_check_clear",
      params: {},
    });
    assert.deepEqual(checkLine({ patients: 0, complete: false }), {
      key: "ihr_packs_check_partial",
      params: {},
    });
  });
});

describe("pages", () => {
  it("steps back to the last page that has rows when a decision empties the current one", () => {
    // 51 waiting, on page 2 (offset 50); the only item there is decided: 50 left
    assert.equal(clampOffset(50, 50), 0);
    assert.equal(clampOffset(100, 51), 50);
    assert.equal(clampOffset(50, 51), 50);
    assert.equal(clampOffset(0, 0), 0);
    assert.equal(clampOffset(50, 0), 0);
  });

  it("counts from 1 and never past the total", () => {
    assert.equal(PAGE_SIZE, 50);
    assert.deepEqual(pageRange(0, 120), {
      from: 1,
      to: 50,
      hasPrev: false,
      hasNext: true,
    });
    assert.deepEqual(pageRange(100, 120), {
      from: 101,
      to: 120,
      hasPrev: true,
      hasNext: false,
    });
    assert.deepEqual(pageRange(0, 0), {
      from: 0,
      to: 0,
      hasPrev: false,
      hasNext: false,
    });
  });
});

describe("history", () => {
  const entry = (over: Partial<PackHistoryEntry> = {}): PackHistoryEntry => ({
    id: 1,
    pack: "care-valuesets",
    version: 2,
    outcome: "applied",
    at: "2026-11-21T10:00:00+05:30",
    by: "start-up",
    counts: { changes_total: 3, created: 3 },
    changes: [
      { item: "a", facility: null, change: "created" },
      { item: "b", facility: null, change: "updated" },
      { item: "c", facility: null, change: "updated" },
    ],
    ...over,
  });

  it("says who: start-up, operator, a named user, a former user", () => {
    assert.deepEqual(historyBy("start-up"), { key: "ihr_packs_by_startup" });
    assert.deepEqual(historyBy("operator"), { key: "ihr_packs_by_operator" });
    assert.deepEqual(historyBy({ user: "u1", username: "priya" }), {
      name: "priya",
    });
    assert.deepEqual(historyBy({ user: null, username: null }), {
      key: "ihr_packs_by_unknown",
    });
    assert.deepEqual(historyBy(undefined), { key: "ihr_packs_by_unknown" });
  });

  it("summarises an apply by its recorded total, not the shown list", () => {
    const s = historySummary(entry({ counts: { changes_total: 700 } }));
    assert.equal(s.total, 700);
    assert.equal(s.listed.length, 3);
    assert.equal(s.notRecorded, false);
  });

  it("says 'not recorded' for an old apply without a change list", () => {
    const s = historySummary(entry({ changes: null, counts: {} }));
    assert.equal(s.notRecorded, true);
    assert.equal(s.total, null);
  });

  it("shows a decision's item and the reason the admin gave", () => {
    const s = historySummary(
      entry({
        outcome: "kept",
        counts: { item: "x-slug", reason: "ticket 4" },
        changes: [],
      }),
    );
    assert.equal(s.item, "x-slug");
    assert.equal(s.reason, "ticket 4");
    assert.equal(s.total, null);
    assert.equal(s.notRecorded, false);
  });

  it("shows no other kind of apply as not recorded", () => {
    const s = historySummary(
      entry({ outcome: "failed", counts: {}, changes: [] }),
    );
    assert.equal(s.notRecorded, false);
  });
});

describe("days and tabs", () => {
  it("shows the day in India time, DD-MM-YYYY", () => {
    // 21-11-2026 23:00 UTC is already 22-11-2026 in India
    assert.equal(sinceText("2026-11-21T23:00:00+00:00"), "22-11-2026");
    assert.equal(sinceText("2026-11-21T10:00:00+05:30"), "21-11-2026");
    assert.equal(sinceText(""), "-");
    assert.equal(sinceText("not a date"), "-");
    assert.equal(sinceText(null), "-");
  });

  it("reads the tab from the address, the first one when it is odd", () => {
    assert.equal(readTab("kept"), "kept");
    assert.equal(readTab("history"), "history");
    assert.equal(readTab("pending"), "pending");
    assert.equal(readTab("x"), "pending");
    assert.equal(readTab(undefined), "pending");
    assert.equal(readTab(["kept"]), "pending");
  });
});

describe("counted strings have a singular and a plural form (P1-T24c review W6)", () => {
  for (const lang of ["en", "hi"]) {
    const strings = JSON.parse(
      readFileSync(
        join(process.cwd(), "..", "..", "deploy", "locale", `${lang}.json`),
        "utf-8",
      ),
    ) as Record<string, string>;
    for (const base of ["ihr_packs_check_patients", "ihr_packs_hist_more"]) {
      it(`${lang}: ${base}_one and ${base}_other, no bare key`, () => {
        assert.ok(strings[`${base}_one`], `${base}_one`);
        assert.ok(strings[`${base}_other`], `${base}_other`);
        assert.equal(strings[base], undefined);
        assert.ok(strings[`${base}_one`].includes("{{count}}"));
      });
    }
  }
});
