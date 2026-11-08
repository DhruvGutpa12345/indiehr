// IndieHR (P1-T7b): the owner's India date style for every date the web app shows.
//   - a full date (day, month and year) is DD-MM-YYYY: "08-11-2026";
//   - a weekday, where upstream shows one, goes in front: "Sun, 08-11-2026";
//   - time is a 12-hour clock with a two-digit hour and AM/PM: "09:30 PM";
//   - date and time together: "08-11-2026, 09:30 PM" (seconds kept where upstream shows them);
//   - headings without a year ("08 Nov", "November 2026", "MM/yyyy" expiry) stay as they are.
//
// The two tables below are EXPLICIT: only a display pattern listed here is rewritten, and
// anything else passes through unchanged. That is what keeps API and input formats
// ("yyyy-MM-dd", "YYYY-MM-DD", "yyyy-MM-dd'T'HH:mm", "HH:mm", "HH:mm:ss" ...) safe.
// scripts/check-date-formats.py reads these tables and fails when an upstream sync brings a
// display pattern that is in neither table nor its allow-list.
//
// Wired in by src/Utils/ihr/indiaDayjs.ts (dayjs) and src/Utils/ihr/indiaDateFns.ts
// (date-fns, through the Vite plugin in indiaDateFnsVite.ts). This file has no imports so
// it can be unit-tested with plain Node.

/** date-fns tokens: dd day, MM month, yyyy year, EEE weekday, hh 12-hour, a = "AM"/"PM". */
export const INDIA_DATE = "dd-MM-yyyy";
export const INDIA_TIME = "hh:mm a";
export const INDIA_TIME_SECONDS = "hh:mm:ss a";
export const INDIA_DATE_TIME = `${INDIA_DATE}, ${INDIA_TIME}`;
export const INDIA_DATE_TIME_SECONDS = `${INDIA_DATE}, ${INDIA_TIME_SECONDS}`;

/** dayjs tokens: DD day, MM month, YYYY year, ddd weekday, hh 12-hour, A = "AM"/"PM" ("a" is lower case in dayjs). */
export const INDIA_DATE_DAYJS = "DD-MM-YYYY";
export const INDIA_TIME_DAYJS = "hh:mm A";
export const INDIA_DATE_TIME_DAYJS = `${INDIA_DATE_DAYJS}, ${INDIA_TIME_DAYJS}`;

// prettier-ignore
export const DATE_FNS_FORMAT_MAP: Readonly<Record<string, string>> = {
  // Full dates
  "dd MMM yyyy": "dd-MM-yyyy",
  "dd/MM/yyyy": "dd-MM-yyyy",
  "dd MMMM, yyyy": "dd-MM-yyyy",
  "dd MMMM yyyy": "dd-MM-yyyy",
  "MMMM d, yyyy": "dd-MM-yyyy",
  "MMMM d yyyy": "dd-MM-yyyy",
  "MMM d, yyyy": "dd-MM-yyyy",
  "MMM dd, yyyy": "dd-MM-yyyy",
  "dd MMM, yyyy": "dd-MM-yyyy",
  "d MMM, yyyy": "dd-MM-yyyy",
  "d MMM yyyy": "dd-MM-yyyy",
  "d MMM yy": "dd-MM-yyyy",
  "d/M/yyyy": "dd-MM-yyyy",
  "P": "dd-MM-yyyy",
  "PP": "dd-MM-yyyy",
  "PPP": "dd-MM-yyyy",
  // Full dates with a weekday
  "PPPP": "EEEE, dd-MM-yyyy",
  "EEE, dd MMM yyyy": "EEE, dd-MM-yyyy",
  "EEE, d MMM yyyy": "EEE, dd-MM-yyyy",
  "dd MMM, yyyy, EEE": "EEE, dd-MM-yyyy",
  "dd MMM yyyy, EEEE": "EEEE, dd-MM-yyyy",
  // Date and time
  "MMM d, yyyy h:mm a": "dd-MM-yyyy, hh:mm a",
  "MMM d, yyyy, h:mm a": "dd-MM-yyyy, hh:mm a",
  "MMM d, yyyy hh:mm a": "dd-MM-yyyy, hh:mm a",
  "d MMM yyyy, hh:mm a": "dd-MM-yyyy, hh:mm a",
  "dd MMM yyyy, hh:mm a": "dd-MM-yyyy, hh:mm a",
  "dd MMM, yyyy h:mm a": "dd-MM-yyyy, hh:mm a",
  "dd MMM, yyyy, hh:mm a": "dd-MM-yyyy, hh:mm a",
  "dd/MM/yyyy hh:mm a": "dd-MM-yyyy, hh:mm a",
  "dd-MM-yyyy HH:mm": "dd-MM-yyyy, hh:mm a",
  "MMMM d, yyyy 'at' h:mm a": "dd-MM-yyyy, hh:mm a",
  "Pp": "dd-MM-yyyy, hh:mm a",
  "PPp": "dd-MM-yyyy, hh:mm a",
  "PPPp": "dd-MM-yyyy, hh:mm a",
  "PPP p": "dd-MM-yyyy, hh:mm a",
  "PP 'at' p": "dd-MM-yyyy, hh:mm a",
  "PPP 'at' p": "dd-MM-yyyy, hh:mm a",
  "PPPPp": "EEEE, dd-MM-yyyy, hh:mm a",
  "PPp zzz": "dd-MM-yyyy, hh:mm a zzz",
  // Date and time with seconds
  "Ppp": "dd-MM-yyyy, hh:mm:ss a",
  "PPpp": "dd-MM-yyyy, hh:mm:ss a",
  "PPPpp": "dd-MM-yyyy, hh:mm:ss a",
  // Time only
  "h:mm a": "hh:mm a",
  "hh:mma": "hh:mm a",
  "h:mm:ss a": "hh:mm:ss a",
  "p": "hh:mm a",
  "pp": "hh:mm:ss a",
};

// prettier-ignore
export const DAYJS_FORMAT_MAP: Readonly<Record<string, string>> = {
  // Full dates (includes both formatDateTime defaults: "DD/MM/YYYY" and "MMM DD, YYYY")
  "DD/MM/YYYY": "DD-MM-YYYY",
  "MMM DD, YYYY": "DD-MM-YYYY",
  "DD MMM YYYY": "DD-MM-YYYY",
  "DD MMM YYYY ": "DD-MM-YYYY ",
  "DD MMMM YYYY": "DD-MM-YYYY",
  "MMMM DD, YYYY": "DD-MM-YYYY",
  "MMMM D, YYYY": "DD-MM-YYYY",
  "DD/MM/YY": "DD-MM-YYYY",
  "DD-MM-YY": "DD-MM-YYYY",
  // Full dates with a weekday
  "ddd, DD MMM YYYY": "ddd, DD-MM-YYYY",
  // Date and time (includes both formatDateTime defaults)
  "hh:mm A; DD/MM/YYYY": "DD-MM-YYYY, hh:mm A",
  "MMM DD, YYYY, hh:mm A": "DD-MM-YYYY, hh:mm A",
  "DD/MM/YYYY hh:mm A": "DD-MM-YYYY, hh:mm A",
  "DD/MM/YYYY, hh:mm A": "DD-MM-YYYY, hh:mm A",
  "DD/MM/YY, hh:mm A": "DD-MM-YYYY, hh:mm A",
  "hh:mm A, DD/MM/YYYY": "DD-MM-YYYY, hh:mm A",
  "DD MMM YYYY, hh:mm A": "DD-MM-YYYY, hh:mm A",
  "DD MMM YYYY, hh:mm a": "DD-MM-YYYY, hh:mm A",
  "DD MMM YYYY h:mm A": "DD-MM-YYYY, hh:mm A",
  "hh:mm A - MMM DD, YYYY": "DD-MM-YYYY, hh:mm A",
  "hh:mm a - DD MMM, YYYY": "DD-MM-YYYY, hh:mm A",
  "MMMM D, YYYY [at] h:mm A": "DD-MM-YYYY, hh:mm A",
  "DD MMM YYYY, ddd, hh:mm A": "ddd, DD-MM-YYYY, hh:mm A",
  "ddd, DD MMM YYYY, HH:mm": "ddd, DD-MM-YYYY, hh:mm A",
  // Time only ("a" prints "pm" in dayjs; the owner's style is "PM")
  "hh:mm a": "hh:mm A",
  "h:mm a": "hh:mm A",
  "h:mm A": "hh:mm A",
};

/** The owner's form of a date-fns display pattern; unknown patterns are returned unchanged. */
export function mapDateFnsFormat<T extends string | undefined>(pattern: T): T {
  if (typeof pattern !== "string") return pattern;
  return (
    Object.prototype.hasOwnProperty.call(DATE_FNS_FORMAT_MAP, pattern)
      ? DATE_FNS_FORMAT_MAP[pattern]
      : pattern
  ) as T;
}

/** The owner's form of a dayjs display pattern; unknown patterns (and no pattern = ISO) are returned unchanged. */
export function mapDayjsFormat<T extends string | undefined>(pattern: T): T {
  if (typeof pattern !== "string") return pattern;
  return (
    Object.prototype.hasOwnProperty.call(DAYJS_FORMAT_MAP, pattern)
      ? DAYJS_FORMAT_MAP[pattern]
      : pattern
  ) as T;
}

// ---------------------------------------------------------------------------
// Replacements for Date.prototype.toLocale{Date,Time,}String at display call sites.
// They take the same options object the upstream call passed, so a call site changes in
// one line: `x.toLocaleDateString("en-US", {` becomes `indiaDateString(x, {`.
// English weekday names, like the rest of the app's date output.

type LocaleOptions = Intl.DateTimeFormatOptions | undefined;

const WEEKDAYS_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WEEKDAYS_LONG = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

const pad2 = (n: number) => String(n).padStart(2, "0");

function toDate(value: Date | string | number): Date {
  return value instanceof Date ? value : new Date(value);
}

/** "08-11-2026" in the device's time zone. */
export function indiaDate(value: Date | string | number): string {
  const d = toDate(value);
  if (isNaN(d.getTime())) return "Invalid Date";
  return `${pad2(d.getDate())}-${pad2(d.getMonth() + 1)}-${d.getFullYear()}`;
}

/** "09:30 PM" (or "09:30:05 PM" with seconds). */
export function indiaTime(
  value: Date | string | number,
  withSeconds = false,
): string {
  const d = toDate(value);
  if (isNaN(d.getTime())) return "Invalid Date";
  const h = d.getHours();
  const hour12 = h % 12 === 0 ? 12 : h % 12;
  const seconds = withSeconds ? `:${pad2(d.getSeconds())}` : "";
  return `${pad2(hour12)}:${pad2(d.getMinutes())}${seconds} ${h < 12 ? "AM" : "PM"}`;
}

/** "08-11-2026, 09:30 PM". */
export function indiaDateTime(
  value: Date | string | number,
  withSeconds = false,
): string {
  const d = toDate(value);
  if (isNaN(d.getTime())) return "Invalid Date";
  return `${indiaDate(d)}, ${indiaTime(d, withSeconds)}`;
}

function weekdayPrefix(d: Date, options: LocaleOptions): string {
  if (options?.weekday === "long") return `${WEEKDAYS_LONG[d.getDay()]}, `;
  if (options?.weekday) return `${WEEKDAYS_SHORT[d.getDay()]}, `;
  return "";
}

const hasDateParts = (o: Intl.DateTimeFormatOptions) =>
  !!(o.day || o.month || o.year || o.weekday || o.dateStyle);
const hasTimeParts = (o: Intl.DateTimeFormatOptions) =>
  !!(o.hour || o.minute || o.second || o.timeStyle);
const isFullDate = (o: Intl.DateTimeFormatOptions) =>
  !!o.dateStyle || !!(o.day && o.month && o.year);

/**
 * Same result as `toLocaleString(locale, options)` in the owner's style: a full date becomes
 * DD-MM-YYYY, a time becomes "09:30 PM", seconds only when asked for. A year-less or
 * month-only request ("08 Nov", "November") is left to the browser, as before.
 * With no date or time options it shows both, like `toLocaleString()`.
 */
export function indiaDateTimeString(
  value: Date | string | number,
  options?: LocaleOptions,
): string {
  const d = toDate(value);
  if (isNaN(d.getTime())) return "Invalid Date";
  const o = options ?? {};
  const wantDate = hasDateParts(o) || !hasTimeParts(o);
  const wantTime = hasTimeParts(o) || !hasDateParts(o);
  if (wantDate && !isFullDate(o) && hasDateParts(o)) {
    return d.toLocaleString("en-IN", o);
  }
  const seconds =
    !!o.second || o.timeStyle === "medium" || o.timeStyle === "long";
  const parts: string[] = [];
  if (wantDate) parts.push(`${weekdayPrefix(d, o)}${indiaDate(d)}`);
  if (wantTime) parts.push(indiaTime(d, seconds));
  return parts.join(", ");
}

/** Replacement for `toLocaleDateString(locale, options)`: the date only, unless time options are given. */
export function indiaDateString(
  value: Date | string | number,
  options?: LocaleOptions,
): string {
  const o = options ?? {};
  if (!hasDateParts(o) && !hasTimeParts(o)) {
    const d = toDate(value);
    return isNaN(d.getTime()) ? "Invalid Date" : indiaDate(d);
  }
  return indiaDateTimeString(value, o);
}

/** Replacement for `toLocaleTimeString(locale, options)`: "09:30 PM" (seconds only when asked for or by default). */
export function indiaTimeString(
  value: Date | string | number,
  options?: LocaleOptions,
): string {
  const d = toDate(value);
  if (isNaN(d.getTime())) return "Invalid Date";
  const o = options ?? {};
  // toLocaleTimeString() with no options shows seconds; with hour/minute options only when asked.
  const seconds = hasTimeParts(o) ? !!o.second : true;
  return indiaTime(d, seconds);
}
