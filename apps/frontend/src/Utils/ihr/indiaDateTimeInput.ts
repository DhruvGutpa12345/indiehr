// IndieHR (P1-T7b): pure rules for our date-and-time box (components/ihr/IndiaDateTimeInput.tsx),
// which replaces the browser's own datetime-local box. Staff type the date as DD-MM-YYYY and
// the time as hh:mm with AM/PM, whatever language the browser is set to.
// The value contract is the one upstream's DateTimeInput had: in and out is an ISO string
// (toISOString) or undefined; min/max are "yyyy-MM-ddTHH:mm" in local time.
// No imports, so it is unit-tested with plain Node (indiaDateTimeInput.test.ts).

export type Meridiem = "AM" | "PM";
/** "" = not chosen yet: a new box has no AM/PM, so an evening time is never saved as morning by default. */
export type MeridiemChoice = Meridiem | "";

export interface DateTimeDraft {
  /** As typed, e.g. "08-11-2026". */
  date: string;
  /** As typed, e.g. "09:30" (12-hour), or "21:30" / "00:15" (24-hour, AM/PM set from it). */
  time: string;
  meridiem: MeridiemChoice;
  /**
   * True when AM/PM was set by a typed 24-hour time rather than chosen by the person. Only
   * then may a later edit of the time change or clear it; a chosen AM/PM is never overwritten.
   */
  meridiemFromTime?: boolean;
}

export type DraftStatus =
  | "empty"
  | "incomplete"
  | "invalid_date"
  | "invalid_time"
  | "choose_am_pm"
  | "before_min"
  | "after_max"
  | "ok";

export interface DraftResult {
  status: DraftStatus;
  /**
   * Set when status is "ok", and also for "before_min" / "after_max": like the browser's
   * datetime-local box, an out-of-range value is still sent on (with the red message shown),
   * so what is saved always matches what is shown.
   */
  iso?: string;
}

export const EMPTY_DRAFT: DateTimeDraft = {
  date: "",
  time: "",
  meridiem: "",
};

const pad2 = (n: number) => String(n).padStart(2, "0");

export const MIN_YEAR = 1900;
export const MAX_YEAR = 2199;

/** Day, month and year from "DD-MM-YYYY" (also "D-M-YYYY", "/" or "." between parts). Null if not a real date. */
export function parseIndiaDate(
  text: string,
): { day: number; month: number; year: number } | null {
  const m = /^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\s*$/.exec(text);
  if (!m) return null;
  const day = Number(m[1]);
  const month = Number(m[2]);
  const year = Number(m[3]);
  if (year < MIN_YEAR || year > MAX_YEAR) return null;
  if (month < 1 || month > 12 || day < 1) return null;
  // Day 0 of the next month is the last day of this one (handles leap years).
  const daysInMonth = new Date(year, month, 0).getDate();
  if (day > daysInMonth) return null;
  return { day, month, year };
}

const TIME_TEXT = /^\s*(\d{1,2})[:.](\d{2})\s*$/;
/** Only a full two-digit hour counts as 24-hour time: "0:30" is half-typed (e.g. "09:30" with the 9 deleted). */
const TIME_TEXT_24 = /^\s*(\d{2})[:.](\d{2})\s*$/;

/** The AM/PM a typed 24-hour time implies: hours 13-23 are PM, 00 is AM; null for 01-12 or unfinished text. */
export function meridiemOf24Hour(text: string): Meridiem | null {
  const m = TIME_TEXT_24.exec(text);
  if (!m || Number(m[2]) > 59) return null;
  const hour = Number(m[1]);
  if (hour === 0) return "AM";
  if (hour >= 13 && hour <= 23) return "PM";
  return null;
}

/**
 * The draft after the time box changed. A 24-hour time ("21:30", "00:15") fills AM/PM only when
 * none was chosen by the person (or the last one came from a typed time); an AM/PM the person
 * chose is kept, and a time that disagrees with it is shown as invalid. When a time-derived
 * AM/PM no longer fits (e.g. "21:30" corrected to "09:30"), it is cleared so AM/PM is asked again.
 */
export function withTypedTime(
  draft: DateTimeDraft,
  time: string,
): DateTimeDraft {
  const implied = meridiemOf24Hour(time);
  const chosenByPerson = draft.meridiem !== "" && !draft.meridiemFromTime;
  if (chosenByPerson) return { ...draft, time };
  if (implied)
    return { ...draft, time, meridiem: implied, meridiemFromTime: true };
  if (draft.meridiemFromTime) {
    return { ...draft, time, meridiem: "", meridiemFromTime: false };
  }
  return { ...draft, time };
}

/**
 * 24-hour hours and minutes from "hh:mm" (hour 01-12) plus AM/PM (12:xx AM is 00:xx, 12:xx PM
 * is 12:xx), or from a 24-hour time (00:xx, 13:xx-23:xx) whose AM/PM is unset or agrees.
 * Null if not a real time, if it disagrees with the AM/PM, or if 01-12 has no AM/PM yet.
 */
export function parseIndiaTime(
  text: string,
  meridiem: MeridiemChoice,
): { hours: number; minutes: number } | null {
  const m = TIME_TEXT.exec(text);
  if (!m) return null;
  const hour = Number(m[1]);
  const minutes = Number(m[2]);
  if (hour > 23 || minutes > 59) return null;
  const implied = meridiemOf24Hour(text);
  if (implied) {
    if (meridiem && meridiem !== implied) return null;
    return { hours: hour, minutes };
  }
  if (!meridiem) return null;
  if (hour < 1 || hour > 12) return null; // "0:30" or a 12-hour time outside 01-12
  return { hours: (hour % 12) + (meridiem === "PM" ? 12 : 0), minutes };
}

/** The draft shown for a stored value (any string Date can read, normally ISO). Null if unreadable. */
export function draftFromValue(value: unknown): DateTimeDraft | null {
  if (typeof value !== "string" || !value) return null;
  const d = new Date(value);
  if (isNaN(d.getTime())) return null;
  const h = d.getHours();
  return {
    date: `${pad2(d.getDate())}-${pad2(d.getMonth() + 1)}-${d.getFullYear()}`,
    time: `${pad2(h % 12 === 0 ? 12 : h % 12)}:${pad2(d.getMinutes())}`,
    meridiem: h < 12 ? "AM" : "PM",
  };
}

/** A local "yyyy-MM-ddTHH:mm" bound (the datetime-local min/max format) as a Date, or null. */
export function parseLocalBound(bound: unknown): Date | null {
  if (typeof bound !== "string") return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(bound);
  if (!m) return null;
  const d = new Date(
    Number(m[1]),
    Number(m[2]) - 1,
    Number(m[3]),
    Number(m[4]),
    Number(m[5]),
  );
  return isNaN(d.getTime()) ? null : d;
}

/** What the draft means: empty, still being typed, wrong, out of range, or a value to save. */
export function evaluateDraft(
  draft: DateTimeDraft,
  bounds: { min?: unknown; max?: unknown } = {},
): DraftResult {
  const dateText = draft.date.trim();
  const timeText = draft.time.trim();
  if (!dateText && !timeText) return { status: "empty" };
  const date = dateText ? parseIndiaDate(dateText) : null;
  if (dateText && !date) {
    // Still typing ("08-11-20") is "incomplete", a finished but impossible date is "invalid".
    return {
      status: /^\d{1,2}[-/.]\d{1,2}[-/.]\d{4}$/.test(dateText)
        ? "invalid_date"
        : "incomplete",
    };
  }
  const time = timeText ? parseIndiaTime(timeText, draft.meridiem) : null;
  if (timeText && !time) {
    if (!/^\d{1,2}[:.]\d{2}$/.test(timeText)) return { status: "incomplete" };
    // A real 01:00-12:59 time with no AM/PM chosen yet: ask for it, never assume one.
    const realHour12 =
      parseIndiaTime(timeText, "AM") && !meridiemOf24Hour(timeText);
    if (!draft.meridiem && realHour12) {
      return { status: date ? "choose_am_pm" : "incomplete" };
    }
    return { status: "invalid_time" };
  }
  if (!date || !time) return { status: "incomplete" };
  const local = new Date(
    date.year,
    date.month - 1,
    date.day,
    time.hours,
    time.minutes,
  );
  // Minute precision, like datetime-local; compare against the bounds at the same precision.
  const min = parseLocalBound(bounds.min);
  const max = parseLocalBound(bounds.max);
  const iso = local.toISOString();
  if (min && local.getTime() < min.getTime()) {
    return { status: "before_min", iso };
  }
  if (max && local.getTime() > max.getTime()) {
    return { status: "after_max", iso };
  }
  return { status: "ok", iso };
}

/**
 * Helps typing: digits get dashes added ("0811" -> "08-11"). The box sends its whole text on
 * every keystroke, so text that already has our own dashes (after DD and MM) is formatted again
 * from its digits; "08-112" becomes "08-11-2". Text with the user's own separators in other
 * places ("8-11-2026", "8/11/2026") is left as typed. Never longer than DD-MM-YYYY.
 */
export function autoDashDate(text: string): string {
  if (!/^(\d{2}-){0,2}\d*$/.test(text)) return text.slice(0, 10);
  const digits = text.replace(/-/g, "").slice(0, 8);
  if (digits.length <= 2) return digits;
  if (digits.length <= 4) return `${digits.slice(0, 2)}-${digits.slice(2)}`;
  return `${digits.slice(0, 2)}-${digits.slice(2, 4)}-${digits.slice(4)}`;
}

/** Same for the time: "0930" becomes "09:30", and "09:305" stays "09:30". */
export function autoColonTime(text: string): string {
  if (!/^(\d{2}:)?\d*$/.test(text)) return text.slice(0, 5);
  const digits = text.replace(/:/g, "").slice(0, 4);
  if (digits.length <= 2) return digits;
  return `${digits.slice(0, 2)}:${digits.slice(2)}`;
}

/**
 * True when two values show the same thing in the box (same local minute), or are both
 * blank or unreadable alike. A parent that makes a fresh timestamp on every render
 * (e.g. `new Date().toISOString()`) then does not count as a new value until the minute changes.
 */
export function sameShownValue(a: unknown, b: unknown): boolean {
  const da = draftFromValue(a);
  const db = draftFromValue(b);
  if (!da || !db) {
    const blank = (v: unknown) => v === undefined || v === null || v === "";
    return (blank(a) && blank(b)) || a === b;
  }
  return (
    da.date === db.date && da.time === db.time && da.meridiem === db.meridiem
  );
}

/** "08-11-2026, 09:30 PM" for a local "yyyy-MM-ddTHH:mm" bound, for messages. */
export function describeBound(bound: unknown): string {
  const d = parseLocalBound(bound);
  if (!d) return "";
  const draft = draftFromValue(d.toISOString());
  return draft ? `${draft.date}, ${draft.time} ${draft.meridiem}` : "";
}
