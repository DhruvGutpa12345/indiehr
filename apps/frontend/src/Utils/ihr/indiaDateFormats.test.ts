import dayjs from "dayjs";
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { enUS } from "date-fns/locale";

import { format, formatDate, formatRelative } from "./indiaDateFns";
import { dateFnsShimFor } from "./indiaDateFnsVite";
import {
  DATE_FNS_FORMAT_MAP,
  DAYJS_FORMAT_MAP,
  indiaDate,
  indiaDateString,
  indiaDateTime,
  indiaDateTimeString,
  indiaTime,
  indiaTimeString,
  mapDateFnsFormat,
  mapDayjsFormat,
} from "./indiaDateFormats";
import indiaDayjsFormat from "./indiaDayjs";

dayjs.extend(indiaDayjsFormat);
dayjs.extend(indiaDayjsFormat); // a second registration must not wrap twice

// Local times, so the tests pass in any time zone. 8 November 2026 is a Sunday.
const EVENING = new Date(2026, 10, 8, 21, 30, 5);
const MORNING = new Date(2026, 0, 5, 9, 5, 0);
const MIDNIGHT = new Date(2026, 0, 5, 0, 15, 0);
const NOON = new Date(2026, 0, 5, 12, 0, 0);

// What a mapped pattern may print for EVENING: the owner's style only.
const OWNER_STYLE =
  /^(?:(?:Sun|Sunday), )?(?:08-11-2026(?:, 09:30(?::05)? PM(?: \S.*)?)?|09:30(?::05)? PM)$/;

describe("date-fns display patterns (through our shim)", () => {
  it("writes full dates as DD-MM-YYYY, whatever upstream wrote", () => {
    for (const p of [
      "dd MMM yyyy",
      "dd/MM/yyyy",
      "dd MMMM, yyyy",
      "MMMM d, yyyy",
      "MMM d, yyyy",
      "MMMM d yyyy",
      "d MMM yyyy",
      "d MMM yy",
      "d/M/yyyy",
    ]) {
      assert.equal(format(EVENING, p), "08-11-2026", p);
    }
    assert.equal(format(MORNING, "MMM d, yyyy"), "05-01-2026");
  });

  it("maps the locale tokens P, PP, PPP and PPPP", () => {
    assert.equal(format(EVENING, "P"), "08-11-2026");
    assert.equal(format(EVENING, "PP"), "08-11-2026");
    assert.equal(format(EVENING, "PPP"), "08-11-2026");
    assert.equal(format(EVENING, "PPPP"), "Sunday, 08-11-2026");
  });

  it("keeps the weekday, in front", () => {
    assert.equal(format(EVENING, "EEE, dd MMM yyyy"), "Sun, 08-11-2026");
    assert.equal(format(EVENING, "EEE, d MMM yyyy"), "Sun, 08-11-2026");
    assert.equal(format(EVENING, "dd MMM yyyy, EEEE"), "Sunday, 08-11-2026");
    assert.equal(format(EVENING, "dd MMM, yyyy, EEE"), "Sun, 08-11-2026");
  });

  it("writes date and time as 08-11-2026, 09:30 PM", () => {
    for (const p of [
      "MMM d, yyyy h:mm a",
      "d MMM yyyy, hh:mm a",
      "dd MMM, yyyy h:mm a",
      "dd/MM/yyyy hh:mm a",
      "dd-MM-yyyy HH:mm",
      "MMMM d, yyyy 'at' h:mm a",
      "PPp",
      "PPP p",
      "PP 'at' p",
      "PPP 'at' p",
    ]) {
      assert.equal(format(EVENING, p), "08-11-2026, 09:30 PM", p);
    }
    assert.equal(format(MORNING, "MMM d, yyyy h:mm a"), "05-01-2026, 09:05 AM");
  });

  it("keeps seconds where upstream shows them", () => {
    assert.equal(format(EVENING, "PPPpp"), "08-11-2026, 09:30:05 PM");
    assert.equal(format(EVENING, "PPpp"), "08-11-2026, 09:30:05 PM");
    assert.equal(format(EVENING, "pp"), "09:30:05 PM");
  });

  it("uses a two-digit 12-hour clock with AM/PM", () => {
    assert.equal(format(EVENING, "h:mm a"), "09:30 PM");
    assert.equal(format(EVENING, "hh:mma"), "09:30 PM");
    assert.equal(format(EVENING, "p"), "09:30 PM");
    assert.equal(format(MORNING, "h:mm a"), "09:05 AM");
    assert.equal(format(MIDNIGHT, "h:mm a"), "12:15 AM");
    assert.equal(format(NOON, "h:mm a"), "12:00 PM");
  });

  it("keeps the time zone name on the hover text", () => {
    assert.match(format(EVENING, "PPp zzz"), /^08-11-2026, 09:30 PM \S/);
  });

  it("never touches API and input formats", () => {
    assert.equal(format(EVENING, "yyyy-MM-dd"), "2026-11-08");
    assert.equal(format(EVENING, "yyyy-MM-dd'T'HH:mm"), "2026-11-08T21:30");
    assert.equal(format(EVENING, "HH:mm"), "21:30");
    assert.equal(format(EVENING, "HH:mm:ss"), "21:30:05");
  });

  it("leaves headings without a year as they are", () => {
    assert.equal(format(EVENING, "dd MMM"), "08 Nov");
    assert.equal(format(EVENING, "EEE, dd MMM"), "Sun, 08 Nov");
    assert.equal(format(EVENING, "do MMMM"), "8th November");
    assert.equal(format(EVENING, "MM/yyyy"), "11/2026");
    assert.equal(format(EVENING, "MMM yyyy"), "Nov 2026");
    assert.equal(format(EVENING, "EEEE"), "Sunday");
    assert.equal(format(EVENING, "yyyy"), "2026");
  });

  it("maps formatDate too (date-fns's other name for format)", () => {
    assert.equal(formatDate(EVENING, "dd/MM/yyyy"), "08-11-2026");
    assert.equal(formatDate(EVENING, "MM/yyyy"), "11/2026");
  });

  it("maps every table entry to the owner's style only", () => {
    for (const [from, to] of Object.entries(DATE_FNS_FORMAT_MAP)) {
      assert.equal(mapDateFnsFormat(from), to);
      assert.match(format(EVENING, from), OWNER_STYLE, from);
    }
  });

  it("passes unknown patterns and missing patterns through", () => {
    assert.equal(mapDateFnsFormat("yyyy-MM-dd"), "yyyy-MM-dd");
    assert.equal(mapDateFnsFormat("toString"), "toString");
    assert.equal(mapDateFnsFormat(undefined), undefined);
  });
});

describe("formatRelative (through our shim)", () => {
  it("shows older dates as DD-MM-YYYY and times as 12-hour", () => {
    assert.equal(
      formatRelative(EVENING, new Date(2026, 10, 20, 10, 0)),
      "08-11-2026",
    );
    assert.equal(
      formatRelative(EVENING, new Date(2026, 10, 8, 23, 0)),
      "today at 09:30 PM",
    );
    assert.equal(
      formatRelative(EVENING, new Date(2026, 10, 9, 10, 0)),
      "yesterday at 09:30 PM",
    );
    assert.equal(
      formatRelative(EVENING, new Date(2026, 10, 12, 10, 0)),
      "last Sunday at 09:30 PM",
    );
    assert.equal(
      formatRelative(MORNING, new Date(2026, 0, 4, 10, 0)),
      "tomorrow at 09:05 AM",
    );
  });

  it("keeps a locale the caller passes", () => {
    assert.equal(
      formatRelative(EVENING, new Date(2026, 10, 20, 10, 0), { locale: enUS }),
      "11/08/2026",
    );
  });
});

describe("dayjs display patterns (through our plugin)", () => {
  it("rewrites both formatDateTime defaults", () => {
    // Utils/utils.ts
    assert.equal(dayjs(EVENING).format("DD/MM/YYYY"), "08-11-2026");
    assert.equal(
      dayjs(EVENING).format("hh:mm A; DD/MM/YYYY"),
      "08-11-2026, 09:30 PM",
    );
    // common/utils.ts
    assert.equal(dayjs(EVENING).format("MMM DD, YYYY"), "08-11-2026");
    assert.equal(
      dayjs(EVENING).format("MMM DD, YYYY, hh:mm A"),
      "08-11-2026, 09:30 PM",
    );
  });

  it("writes dates, weekdays and times in the owner's style", () => {
    const d = dayjs(EVENING);
    assert.equal(d.format("DD MMM YYYY"), "08-11-2026");
    assert.equal(d.format("DD MMMM YYYY"), "08-11-2026");
    assert.equal(d.format("MMMM D, YYYY"), "08-11-2026");
    assert.equal(d.format("DD/MM/YY"), "08-11-2026");
    assert.equal(d.format("ddd, DD MMM YYYY"), "Sun, 08-11-2026");
    assert.equal(d.format("DD MMM YYYY, hh:mm A"), "08-11-2026, 09:30 PM");
    assert.equal(d.format("DD MMM YYYY, hh:mm a"), "08-11-2026, 09:30 PM");
    assert.equal(d.format("MMMM D, YYYY [at] h:mm A"), "08-11-2026, 09:30 PM");
    assert.equal(
      d.format("ddd, DD MMM YYYY, HH:mm"),
      "Sun, 08-11-2026, 09:30 PM",
    );
    assert.equal(
      d.format("DD MMM YYYY, ddd, hh:mm A"),
      "Sun, 08-11-2026, 09:30 PM",
    );
    assert.equal(d.format("hh:mm a"), "09:30 PM");
    assert.equal(dayjs(MORNING).format("h:mm A"), "09:05 AM");
  });

  it("maps every table entry to the owner's style only", () => {
    for (const [from, to] of Object.entries(DAYJS_FORMAT_MAP)) {
      assert.equal(mapDayjsFormat(from), to);
      assert.match(dayjs(EVENING).format(from).trim(), OWNER_STYLE, from);
    }
  });

  it("never touches API formats or the ISO default", () => {
    const d = dayjs(EVENING);
    assert.equal(d.format("YYYY-MM-DD"), "2026-11-08");
    assert.equal(d.format("HH:mm"), "21:30");
    assert.equal(d.format("DD-MM-YYYY"), "08-11-2026");
    assert.match(d.format(), /^2026-11-08T21:30:05[+-]\d{2}:\d{2}$/);
  });
});

describe("replacements for toLocale*String", () => {
  it("shows full dates as DD-MM-YYYY", () => {
    const o = { day: "2-digit", month: "short", year: "numeric" } as const;
    assert.equal(indiaDateString(EVENING, o), "08-11-2026");
    assert.equal(indiaDateString(EVENING), "08-11-2026");
    assert.equal(indiaDate(EVENING), "08-11-2026");
    assert.equal(
      indiaDateString(EVENING, {
        weekday: "long",
        year: "numeric",
        month: "long",
        day: "numeric",
      }),
      "Sunday, 08-11-2026",
    );
  });

  it("shows date and time, with seconds only when asked for", () => {
    assert.equal(indiaDateTimeString(EVENING), "08-11-2026, 09:30 PM");
    assert.equal(indiaDateTime(EVENING), "08-11-2026, 09:30 PM");
    assert.equal(indiaDateTime(EVENING, true), "08-11-2026, 09:30:05 PM");
    assert.equal(
      indiaDateTimeString(EVENING, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
      }),
      "08-11-2026, 09:30:05 PM",
    );
    assert.equal(
      indiaDateTimeString(EVENING, { dateStyle: "long", timeStyle: "short" }),
      "08-11-2026, 09:30 PM",
    );
  });

  it("shows times as 09:30 PM", () => {
    const o = { hour: "2-digit", minute: "2-digit" } as const;
    assert.equal(indiaTimeString(EVENING, o), "09:30 PM");
    assert.equal(indiaTimeString(EVENING), "09:30:05 PM");
    assert.equal(indiaTime(MIDNIGHT), "12:15 AM");
    assert.equal(indiaTime(NOON), "12:00 PM");
  });

  it("accepts strings and numbers, and reports bad input like the browser", () => {
    assert.equal(indiaDate(EVENING.toISOString()), "08-11-2026");
    assert.equal(indiaDate(EVENING.getTime()), "08-11-2026");
    assert.equal(indiaDate("not a date"), "Invalid Date");
    assert.equal(indiaDateTime(""), "Invalid Date");
  });

  it("leaves month-only and year-less requests to the browser", () => {
    const o = { month: "long" } as const;
    assert.equal(
      indiaDateString(EVENING, o),
      EVENING.toLocaleString("en-IN", o),
    );
  });
});

describe("Vite redirect of date-fns", () => {
  const SRC = "/repo/apps/frontend/src";
  const SHIM = `${SRC}/Utils/ihr/indiaDateFns.ts`;

  it("sends our source files to the shim", () => {
    assert.equal(dateFnsShimFor(SRC, "date-fns", `${SRC}/App.tsx`), SHIM);
    assert.equal(
      dateFnsShimFor(SRC, "date-fns", `${SRC}/pages/X.tsx?v=1`),
      SHIM,
    );
    assert.equal(dateFnsShimFor(SRC, "date-fns/format", `${SRC}/a.ts`), SHIM);
    assert.equal(
      dateFnsShimFor("C:\\repo\\src", "date-fns", "C:\\repo\\src\\a.ts"),
      "C:/repo/src/Utils/ihr/indiaDateFns.ts",
    );
  });

  it("leaves the shim, node_modules, other folders and other packages alone", () => {
    assert.equal(dateFnsShimFor(SRC, "date-fns", SHIM), null);
    assert.equal(
      dateFnsShimFor(
        SRC,
        "date-fns",
        "/repo/apps/frontend/node_modules/react-day-picker/dist/index.js",
      ),
      null,
    );
    assert.equal(
      dateFnsShimFor(SRC, "date-fns", "/repo/apps/frontend/apps/x/src/a.ts"),
      null,
    );
    assert.equal(
      dateFnsShimFor(SRC, "date-fns", "/repo/apps/frontend/src2/a.ts"),
      null,
    );
    assert.equal(dateFnsShimFor(SRC, "date-fns", "\0virtual:x"), null);
    assert.equal(dateFnsShimFor(SRC, "date-fns", undefined), null);
    assert.equal(dateFnsShimFor(SRC, "date-fns/locale", `${SRC}/a.ts`), null);
    assert.equal(dateFnsShimFor(SRC, "dayjs", `${SRC}/a.ts`), null);
  });
});
