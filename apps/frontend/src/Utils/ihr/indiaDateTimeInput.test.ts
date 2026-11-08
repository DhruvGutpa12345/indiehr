import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  EMPTY_DRAFT,
  autoColonTime,
  autoDashDate,
  describeBound,
  draftFromValue,
  evaluateDraft,
  meridiemOf24Hour,
  parseIndiaDate,
  parseIndiaTime,
  parseLocalBound,
  sameShownValue,
  withTypedTime,
} from "./indiaDateTimeInput";

// Local times, so the tests pass in any time zone.
const EVENING = new Date(2026, 10, 8, 21, 30);

describe("typed date DD-MM-YYYY", () => {
  it("reads real dates", () => {
    assert.deepEqual(parseIndiaDate("08-11-2026"), {
      day: 8,
      month: 11,
      year: 2026,
    });
    assert.deepEqual(parseIndiaDate("8/1/2026"), {
      day: 8,
      month: 1,
      year: 2026,
    });
    assert.deepEqual(parseIndiaDate("29-02-2028"), {
      day: 29,
      month: 2,
      year: 2028,
    });
  });

  it("refuses impossible dates", () => {
    for (const text of [
      "29-02-2026",
      "31-04-2026",
      "00-01-2026",
      "32-01-2026",
      "10-13-2026",
      "10-00-2026",
      "01-01-0026",
      "11-08-26",
      "2026-11-08",
      "",
    ]) {
      assert.equal(parseIndiaDate(text), null, text);
    }
  });
});

describe("typed time hh:mm with AM/PM", () => {
  it("turns the 12-hour clock into hours and minutes", () => {
    assert.deepEqual(parseIndiaTime("09:30", "PM"), { hours: 21, minutes: 30 });
    assert.deepEqual(parseIndiaTime("9:05", "AM"), { hours: 9, minutes: 5 });
    assert.deepEqual(parseIndiaTime("12:15", "AM"), { hours: 0, minutes: 15 });
    assert.deepEqual(parseIndiaTime("12:00", "PM"), { hours: 12, minutes: 0 });
  });

  it("refuses impossible times", () => {
    for (const text of ["24:00", "09:60", "0930", "9:5", "123:00"]) {
      assert.equal(parseIndiaTime(text, "AM"), null, text);
    }
  });

  it("needs AM or PM for 01:00-12:59", () => {
    assert.equal(parseIndiaTime("09:30", ""), null);
    assert.equal(parseIndiaTime("12:15", ""), null);
  });

  it("also reads 24-hour time, and refuses one that disagrees with AM/PM", () => {
    assert.deepEqual(parseIndiaTime("21:30", ""), { hours: 21, minutes: 30 });
    assert.deepEqual(parseIndiaTime("21:30", "PM"), { hours: 21, minutes: 30 });
    assert.deepEqual(parseIndiaTime("13:00", ""), { hours: 13, minutes: 0 });
    assert.deepEqual(parseIndiaTime("00:15", ""), { hours: 0, minutes: 15 });
    assert.deepEqual(parseIndiaTime("00:15", "AM"), { hours: 0, minutes: 15 });
    assert.equal(parseIndiaTime("21:30", "AM"), null);
    assert.equal(parseIndiaTime("00:15", "PM"), null);
  });

  it("tells which AM/PM a 24-hour time means", () => {
    assert.equal(meridiemOf24Hour("21:30"), "PM");
    assert.equal(meridiemOf24Hour("13:00"), "PM");
    assert.equal(meridiemOf24Hour("23:59"), "PM");
    assert.equal(meridiemOf24Hour("00:15"), "AM");
    // A one-digit hour is half-typed (e.g. "09:30" with the 9 deleted), never 24-hour time.
    assert.equal(meridiemOf24Hour("0:15"), null);
    for (const text of ["09:30", "12:15", "01:00", "24:00", "21:3", "21:60"]) {
      assert.equal(meridiemOf24Hour(text), null, text);
    }
  });

  it("sets AM/PM when a 24-hour time is typed, and keeps it otherwise", () => {
    const blank = { date: "08-11-2026", time: "", meridiem: "" } as const;
    assert.deepEqual(withTypedTime(blank, "21:30"), {
      ...blank,
      time: "21:30",
      meridiem: "PM",
      meridiemFromTime: true,
    });
    // An AM/PM the person chose is never overwritten; the mismatch shows as invalid.
    assert.deepEqual(withTypedTime({ ...blank, meridiem: "PM" }, "00:15"), {
      ...blank,
      time: "00:15",
      meridiem: "PM",
    });
    assert.equal(
      evaluateDraft(withTypedTime({ ...blank, meridiem: "PM" }, "00:15"))
        .status,
      "invalid_time",
    );
    assert.deepEqual(withTypedTime(blank, "21:3"), { ...blank, time: "21:3" });
    assert.deepEqual(withTypedTime({ ...blank, meridiem: "PM" }, "09:30"), {
      ...blank,
      time: "09:30",
      meridiem: "PM",
    });
    // The stored AM/PM then matches the typed time, so the value is the 24-hour one.
    assert.deepEqual(evaluateDraft(withTypedTime(blank, "21:30")), {
      status: "ok",
      iso: EVENING.toISOString(),
    });
    assert.deepEqual(evaluateDraft(withTypedTime(blank, "00:15")), {
      status: "ok",
      iso: new Date(2026, 10, 8, 0, 15).toISOString(),
    });
  });
});

describe("AM/PM in a new box", () => {
  it("starts with no AM/PM", () => {
    assert.equal(EMPTY_DRAFT.meridiem, "");
  });

  it("asks for AM or PM instead of assuming one", () => {
    const d = { date: "08-11-2026", time: "09:30", meridiem: "" } as const;
    assert.deepEqual(evaluateDraft(d), { status: "choose_am_pm" });
    assert.deepEqual(evaluateDraft({ ...d, time: "12:15" }), {
      status: "choose_am_pm",
    });
    // Without a date it is simply not finished yet.
    assert.equal(evaluateDraft({ ...d, date: "" }).status, "incomplete");
    assert.equal(evaluateDraft({ ...d, time: "09:3" }).status, "incomplete");
    // A wrong time is still wrong, AM/PM or not.
    assert.equal(evaluateDraft({ ...d, time: "09:60" }).status, "invalid_time");
  });

  it("reads 12:xx AM as just after midnight and 12:xx PM as just after noon", () => {
    const d = { date: "08-11-2026", time: "12:15" } as const;
    assert.deepEqual(evaluateDraft({ ...d, meridiem: "AM" }), {
      status: "ok",
      iso: new Date(2026, 10, 8, 0, 15).toISOString(),
    });
    assert.deepEqual(evaluateDraft({ ...d, meridiem: "PM" }), {
      status: "ok",
      iso: new Date(2026, 10, 8, 12, 15).toISOString(),
    });
  });

  it("keeps the AM/PM of a loaded value", () => {
    const noon = new Date(2026, 10, 8, 12, 15).toISOString();
    assert.equal(draftFromValue(noon)?.meridiem, "PM");
    assert.equal(draftFromValue(EVENING.toISOString())?.meridiem, "PM");
    const morning = new Date(2026, 10, 8, 9, 30).toISOString();
    assert.equal(draftFromValue(morning)?.meridiem, "AM");
  });
});

describe("value in, value out", () => {
  it("shows a stored ISO value in the owner's style", () => {
    assert.deepEqual(draftFromValue(EVENING.toISOString()), {
      date: "08-11-2026",
      time: "09:30",
      meridiem: "PM",
    });
    const midnight = new Date(2026, 0, 5, 0, 15).toISOString();
    assert.deepEqual(draftFromValue(midnight), {
      date: "05-01-2026",
      time: "12:15",
      meridiem: "AM",
    });
    assert.equal(draftFromValue(""), null);
    assert.equal(draftFromValue(undefined), null);
    assert.equal(draftFromValue("rubbish"), null);
  });

  it("gives back the same ISO value it was given", () => {
    const iso = EVENING.toISOString();
    const draft = draftFromValue(iso);
    assert.ok(draft);
    assert.deepEqual(evaluateDraft(draft), { status: "ok", iso });
  });

  it("says why there is no value yet", () => {
    assert.equal(evaluateDraft(EMPTY_DRAFT).status, "empty");
    const d = { date: "08-11-2026", time: "09:30", meridiem: "PM" } as const;
    assert.equal(evaluateDraft({ ...d, time: "" }).status, "incomplete");
    assert.equal(evaluateDraft({ ...d, date: "" }).status, "incomplete");
    const partDate = { ...d, date: "08-11-20" };
    assert.equal(evaluateDraft(partDate).status, "incomplete");
    assert.equal(evaluateDraft({ ...d, time: "09:3" }).status, "incomplete");
    assert.equal(
      evaluateDraft({ ...d, date: "31-11-2026" }).status,
      "invalid_date",
    );
    assert.equal(evaluateDraft({ ...d, time: "24:30" }).status, "invalid_time");
    // 24-hour 13:30 with PM chosen is fine; with AM it disagrees.
    assert.equal(evaluateDraft({ ...d, time: "13:30" }).status, "ok");
    assert.equal(
      evaluateDraft({ ...d, time: "13:30", meridiem: "AM" }).status,
      "invalid_time",
    );
  });

  it("keeps to min and max (datetime-local format, local time)", () => {
    const d = { date: "08-11-2026", time: "09:30", meridiem: "PM" } as const;
    const max = "2026-11-08T21:29";
    assert.equal(evaluateDraft(d, { max }).status, "after_max");
    // Out of range still carries the value, as the browser box sent it on.
    const iso = EVENING.toISOString();
    assert.deepEqual(evaluateDraft(d, { max }), { status: "after_max", iso });
    assert.deepEqual(evaluateDraft(d, { min: "2026-11-08T21:31" }), {
      status: "before_min",
      iso,
    });
    // Blank, half-typed and impossible text carry no value.
    for (const bad of [
      EMPTY_DRAFT,
      { ...d, date: "08-11-20" },
      { ...d, date: "31-11-2026" },
      { ...d, time: "09:60" },
      { ...d, meridiem: "" as const },
    ]) {
      assert.equal(evaluateDraft(bad, { max }).iso, undefined);
    }
    assert.equal(evaluateDraft(d, { max: "2026-11-08T21:30" }).status, "ok");
    const min = "2026-11-08T21:31";
    assert.equal(evaluateDraft(d, { min }).status, "before_min");
    assert.equal(evaluateDraft(d, { min: "2026-11-08T21:30" }).status, "ok");
    assert.equal(evaluateDraft(d, { min: "", max: undefined }).status, "ok");
  });

  it("reads and describes the bounds", () => {
    assert.equal(
      parseLocalBound("2026-11-08T21:30")?.getTime(),
      EVENING.getTime(),
    );
    assert.equal(parseLocalBound("08-11-2026"), null);
    assert.equal(describeBound("2026-11-08T21:30"), "08-11-2026, 09:30 PM");
    assert.equal(describeBound(undefined), "");
  });
});

describe("typing help", () => {
  it("adds dashes to a date typed as digits", () => {
    assert.equal(autoDashDate("08"), "08");
    assert.equal(autoDashDate("0811"), "08-11");
    assert.equal(autoDashDate("08112026"), "08-11-2026");
    assert.equal(autoDashDate("081120269"), "08-11-2026");
    assert.equal(autoDashDate("08-1"), "08-1");
    assert.equal(autoDashDate("8/11/2026"), "8/11/2026");
    assert.equal(autoDashDate("8-11-2026"), "8-11-2026");
    assert.equal(autoDashDate("08-112"), "08-11-2");
  });

  it("keeps the dashes right while typed one key at a time", () => {
    // The box sends its whole text, including our dashes, on every keystroke.
    const typed = [..."08112026"].reduce(
      (acc, ch) => autoDashDate(acc + ch),
      "",
    );
    assert.equal(typed, "08-11-2026");
    // Extra digits after a full date are dropped.
    assert.equal(autoDashDate("08-11-20269"), "08-11-2026");
    // Backspace over a dash.
    assert.equal(autoDashDate("08-"), "08");
    assert.equal(autoDashDate("08-11-"), "08-11");
  });

  it("adds the colon to a time typed as digits", () => {
    assert.equal(autoColonTime("09"), "09");
    assert.equal(autoColonTime("0930"), "09:30");
    assert.equal(autoColonTime("9:30"), "9:30");
    const typed = [..."0930"].reduce((acc, ch) => autoColonTime(acc + ch), "");
    assert.equal(typed, "09:30");
    assert.equal(autoColonTime("09:305"), "09:30");
    assert.equal(autoColonTime("09:"), "09");
  });
});

describe("telling a new value from the same one again", () => {
  it("treats the same minute as the same value", () => {
    const a = new Date(2026, 10, 8, 21, 30, 5, 120).toISOString();
    const b = new Date(2026, 10, 8, 21, 30, 59, 999).toISOString();
    const c = new Date(2026, 10, 8, 21, 31).toISOString();
    assert.equal(sameShownValue(a, b), true);
    assert.equal(sameShownValue(a, c), false);
    assert.equal(sameShownValue(undefined, ""), true);
    assert.equal(sameShownValue(undefined, a), false);
    assert.equal(sameShownValue("not a date", "not a date"), true);
  });

  it("never flips a chosen AM/PM while the hour is retyped (review P1-T7b)", () => {
    const d = { date: "08-11-2026", time: "09:30", meridiem: "PM" } as const;
    let draft = withTypedTime(d, "0:30"); // the 9 deleted
    assert.equal(draft.meridiem, "PM");
    assert.notEqual(evaluateDraft(draft).status, "ok");
    draft = withTypedTime(draft, "08:30");
    assert.equal(draft.meridiem, "PM");
    assert.deepEqual(evaluateDraft(draft), {
      status: "ok",
      iso: new Date(2026, 10, 8, 20, 30).toISOString(),
    });
  });

  it("asks for AM/PM again when a typed 24-hour time is corrected to a 12-hour one", () => {
    const blank = { date: "08-11-2026", time: "", meridiem: "" } as const;
    const evening = withTypedTime(blank, "21:30");
    assert.equal(evening.meridiem, "PM");
    const corrected = withTypedTime(evening, "09:30");
    assert.equal(corrected.meridiem, "");
    assert.equal(evaluateDraft(corrected).status, "choose_am_pm");
  });
});
