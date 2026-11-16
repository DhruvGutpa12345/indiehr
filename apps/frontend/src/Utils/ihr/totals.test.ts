// IndieHR P1-T11c3 (design-c.md §7, §13.5). Run: node --import tsx --test src/Utils/ihr/totals.test.ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

import {
  RANGE_ERROR_KEYS,
  type Totals,
  buildQuery,
  checkRange,
  defaultRange,
  fieldErrorsOf,
  humanize,
  parseBy,
  parseDay,
  readUrlState,
  tileLabelKey,
  tiles,
  todayIst,
} from "./totals";

// 16-11-2026 12:00 UTC is 17:30 IST the same day
const NOW = Date.UTC(2026, 10, 16, 12, 0, 0);

function answer(over: Partial<Totals> = {}): Totals {
  return {
    from: "01-11-2026",
    to: "16-11-2026",
    by: "day",
    time_zone: "Asia/Kolkata",
    currency: "INR",
    encounters: [
      { encounter_class: "amb", status: "completed", count: 40 },
      { encounter_class: "amb", status: "cancelled", count: 5 },
      { encounter_class: "imp", status: "in_progress", count: 7 },
      { encounter_class: "imp", status: "entered_in_error", count: 2 },
    ],
    inpatients_now: 7,
    beds: { total: 30, occupied: 9 },
    invoices_by_status: [{ status: "issued", count: 3, amount: "1000.00" }],
    collected_by_method: [{ method: "cash", count: 2, amount: "500.00" }],
    refunded: "100.50",
    outstanding_now: "12345678.00",
    series: [
      {
        period: "01-11-2026",
        admissions: 2,
        discharges: 1,
        invoiced: "100000.10",
        collected: "50000.20",
      },
      {
        period: "02-11-2026",
        admissions: 3,
        discharges: 0,
        invoiced: "250000.20",
        collected: "0.00",
      },
    ],
    ...over,
  };
}

const tile = (t: Totals, key: string) => tiles(t).find((x) => x.key === key);

describe("dates in India time", () => {
  it("reads DD-MM-YYYY and refuses what is not a day", () => {
    assert.deepEqual(parseDay("16-11-2026"), {
      day: 16,
      month: 11,
      year: 2026,
    });
    assert.deepEqual(parseDay("1-2-2026"), { day: 1, month: 2, year: 2026 });
    assert.equal(parseDay("31-02-2026"), null);
    assert.equal(parseDay("2026-11-16"), null);
    assert.equal(parseDay("16/11/2026"), null);
    assert.equal(parseDay(""), null);
    assert.deepEqual(parseDay("29-02-2028"), { day: 29, month: 2, year: 2028 });
    assert.equal(parseDay("29-02-2026"), null);
  });

  it("today is India's day, not the browser's", () => {
    // 20:00 UTC on the 15th is already the 16th in India
    assert.deepEqual(todayIst(Date.UTC(2026, 10, 15, 20, 0)), {
      day: 16,
      month: 11,
      year: 2026,
    });
    assert.deepEqual(todayIst(Date.UTC(2026, 10, 15, 18, 29)), {
      day: 15,
      month: 11,
      year: 2026,
    });
  });

  it("defaults to this month: its first day to today", () => {
    assert.deepEqual(defaultRange(NOW), {
      from: "01-11-2026",
      to: "16-11-2026",
    });
    assert.deepEqual(defaultRange(Date.UTC(2027, 0, 1, 1, 0)), {
      from: "01-01-2027",
      to: "01-01-2027",
    });
  });
});

describe("range rules mirror totals.parse_range", () => {
  it("accepts a normal range and normalises it", () => {
    const ok = checkRange("1-11-2026", "16-11-2026", NOW);
    assert.deepEqual(ok.errors, {});
    assert.deepEqual(ok.range, { from: "01-11-2026", to: "16-11-2026" });
  });

  it("refuses text that is not DD-MM-YYYY", () => {
    const bad = checkRange("2026-11-01", "xx", NOW);
    assert.equal(bad.range, null);
    assert.equal(bad.errors.from, RANGE_ERROR_KEYS.format);
    assert.equal(bad.errors.to, RANGE_ERROR_KEYS.format);
  });

  it("refuses from after to", () => {
    const bad = checkRange("16-11-2026", "15-11-2026", NOW);
    assert.equal(bad.range, null);
    assert.equal(bad.errors.to, RANGE_ERROR_KEYS.order);
    assert.deepEqual(checkRange("16-11-2026", "16-11-2026", NOW).errors, {});
  });

  it("allows 366 days and refuses 367 (a 400-day range sends no request)", () => {
    // 01-01-2026 .. 01-01-2027 is 366 days
    assert.deepEqual(checkRange("01-01-2026", "01-01-2027", NOW).errors, {});
    const long = checkRange("01-01-2026", "02-01-2027", NOW);
    assert.equal(long.range, null);
    assert.equal(long.errors.to, RANGE_ERROR_KEYS.too_long);
    const fourHundred = checkRange("01-10-2025", "05-11-2026", NOW);
    assert.equal(fourHundred.errors.to, RANGE_ERROR_KEYS.too_long);
  });

  it("a leap year counts its 366th day", () => {
    assert.deepEqual(checkRange("01-01-2024", "31-12-2024", NOW).errors, {});
  });

  it("refuses a start before 01-01-2000", () => {
    assert.equal(
      checkRange("31-12-1999", "01-01-2000", NOW).errors.from,
      RANGE_ERROR_KEYS.before_min,
    );
    assert.deepEqual(checkRange("01-01-2000", "01-01-2000", NOW).errors, {});
    // even when the other box is unreadable
    assert.equal(
      checkRange("31-12-1999", "x", NOW).errors.from,
      RANGE_ERROR_KEYS.before_min,
    );
  });

  it("refuses an end more than 366 days after today", () => {
    // today + 366 days is 17-11-2027
    assert.deepEqual(checkRange("01-01-2027", "17-11-2027", NOW).errors, {});
    assert.equal(
      checkRange("01-01-2027", "18-11-2027", NOW).errors.to,
      RANGE_ERROR_KEYS.after_today,
    );
  });
});

describe("address state and the query", () => {
  it("keeps from, to and by in the address, with defaults", () => {
    assert.deepEqual(readUrlState({}, NOW), {
      from: "01-11-2026",
      to: "16-11-2026",
      by: "day",
    });
    assert.deepEqual(
      readUrlState({ from: "01-10-2026", to: "31-10-2026", by: "month" }, NOW),
      { from: "01-10-2026", to: "31-10-2026", by: "month" },
    );
    assert.equal(parseBy("year"), "day");
    assert.equal(parseBy(["month"]), "day");
  });

  it("builds the query the endpoint reads", () => {
    assert.deepEqual(
      buildQuery({
        facility: "f1",
        from: "01-11-2026",
        to: "16-11-2026",
        by: "month",
      }),
      { facility: "f1", from: "01-11-2026", to: "16-11-2026", by: "month" },
    );
  });

  it("reads the 400's field messages", () => {
    assert.deepEqual(fieldErrorsOf({ to: "At most 366 days." }), {
      to: "At most 366 days.",
    });
    assert.deepEqual(fieldErrorsOf({ detail: "No." }), { other: "No." });
    assert.deepEqual(fieldErrorsOf(undefined), {});
  });
});

describe("tiles (Q-K52 A, SS6)", () => {
  it("visits leave out cancelled and entered-in-error", () => {
    assert.equal(tile(answer(), "visits")?.value, 47);
  });

  it("invoiced and collected are sums of the series, exactly", () => {
    assert.equal(tile(answer(), "invoiced")?.value, "350000.30");
    assert.equal(tile(answer(), "collected")?.value, "50000.20");
  });

  it("admissions and discharges are sums of the series", () => {
    assert.equal(tile(answer(), "admissions")?.value, 5);
    assert.equal(tile(answer(), "discharges")?.value, 1);
  });

  it("the rest are as the answer states them", () => {
    const t = answer();
    assert.equal(tile(t, "inpatients_now")?.value, 7);
    assert.equal(tile(t, "refunded")?.value, "100.50");
    assert.equal(tile(t, "outstanding_now")?.value, "12345678.00");
    const beds = tile(t, "beds");
    assert.equal(beds?.value, 9);
    assert.equal(beds?.of, 30);
    assert.equal(tiles(t).length, 9);
  });

  it("a real zero is a zero, a missing value stays missing (never ₹0.00)", () => {
    const zero = answer({ encounters: [], series: [] });
    assert.equal(tile(zero, "visits")?.value, 0);
    assert.equal(tile(zero, "invoiced")?.value, "0.00");
    const missing = answer({
      series: undefined as never,
      refunded: undefined as never,
      outstanding_now: "" as never,
      beds: undefined as never,
      inpatients_now: undefined as never,
      encounters: undefined as never,
    });
    for (const key of [
      "visits",
      "admissions",
      "discharges",
      "invoiced",
      "collected",
      "refunded",
      "outstanding_now",
      "inpatients_now",
      "beds",
    ]) {
      assert.equal(tile(missing, key)?.value, null, key);
    }
    // one unreadable amount in the series makes the whole sum missing, not a partial sum
    const partial = answer();
    partial.series[1].invoiced = "n/a";
    assert.equal(tile(partial, "invoiced")?.value, null);
    assert.equal(tile(partial, "collected")?.value, "50000.20");
  });

  it("every tile has a label in our strings", () => {
    const en = JSON.parse(
      readFileSync(
        join(process.cwd(), "..", "..", "deploy", "locale", "en.json"),
        "utf-8",
      ),
    ) as Record<string, string>;
    for (const t of tiles(answer())) {
      assert.ok(en[tileLabelKey(t.key)], tileLabelKey(t.key));
    }
    for (const key of Object.values(RANGE_ERROR_KEYS)) {
      assert.ok(en[key], key);
    }
  });
});

describe("words for codes", () => {
  it("turns a code into a sentence start", () => {
    assert.equal(humanize("entered_in_error"), "Entered in error");
    assert.equal(humanize("cash"), "Cash");
    assert.equal(humanize(""), "-");
  });
});
