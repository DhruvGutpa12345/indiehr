// IndieHR P1-T11c3 (design-c.md §7, §13.5): the Owner's totals page rules. Pure (no React, no
// "@/" imports), so `node --import tsx --test` runs totals.test.ts.
//
// The backend (plugins/ihr_platform/ihr_access/totals.py, parse_range) is the authority; the
// range rules here mirror it so the page can name a mistake without a request. A figure the
// answer does not carry stays missing (null): it is never shown as a made-up zero.
import Decimal from "decimal.js";

export const DAY_RE = /^(\d{1,2})-(\d{1,2})-(\d{4})$/;
export const MAX_DAYS = 366;
/** totals.MAX_DAYS_AHEAD */
export const MAX_DAYS_AHEAD = 366;
/** totals.MIN_DAY: 01-01-2000 */
export const MIN_DAY = { day: 1, month: 1, year: 2000 } as const;
const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

export type By = "day" | "month";

export interface Ymd {
  day: number;
  month: number;
  year: number;
}

const pad2 = (n: number) => String(n).padStart(2, "0");

/** "DD-MM-YYYY" (also D-M-YYYY) as a real calendar day, or null. */
export function parseDay(text: string): Ymd | null {
  const m = DAY_RE.exec(text.trim());
  if (!m) return null;
  const day = Number(m[1]);
  const month = Number(m[2]);
  const year = Number(m[3]);
  if (month < 1 || month > 12 || day < 1 || year < 1000) return null;
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  if (day > last) return null;
  return { day, month, year };
}

export function formatDay(d: Ymd): string {
  return `${pad2(d.day)}-${pad2(d.month)}-${String(d.year).padStart(4, "0")}`;
}

/** Days since 1970-01-01 of a calendar day (no time zone in it). */
function dayNumber(d: Ymd): number {
  return Math.floor(Date.UTC(d.year, d.month - 1, d.day) / DAY_MS);
}

/** Today in India (IST, UTC+5:30, no daylight time) from a clock reading in ms. */
export function todayIst(nowMs: number): Ymd {
  const d = new Date(nowMs + IST_OFFSET_MS);
  return {
    day: d.getUTCDate(),
    month: d.getUTCMonth() + 1,
    year: d.getUTCFullYear(),
  };
}

/** The default range: this month in IST, its first day to today (design §7). */
export function defaultRange(nowMs: number): { from: string; to: string } {
  const today = todayIst(nowMs);
  return {
    from: formatDay({ day: 1, month: today.month, year: today.year }),
    to: formatDay(today),
  };
}

/** Keys of our own strings (deploy/locale/en.json) for the client's range errors. */
export const RANGE_ERROR_KEYS = {
  format: "ihr_totals_error_format",
  before_min: "ihr_totals_error_before_min",
  after_today: "ihr_totals_error_after_today",
  order: "ihr_totals_error_order",
  too_long: "ihr_totals_error_too_long",
} as const;

export interface RangeCheck {
  errors: Partial<Record<"from" | "to", string>>;
  /** the normalised DD-MM-YYYY pair, only when there is no error */
  range: { from: string; to: string } | null;
}

/** The client's mirror of totals.parse_range. `nowMs` is the clock (injected for tests). */
export function checkRange(
  from: string,
  to: string,
  nowMs: number,
): RangeCheck {
  const errors: RangeCheck["errors"] = {};
  const start = parseDay(from);
  const end = parseDay(to);
  if (!start) errors.from = RANGE_ERROR_KEYS.format;
  if (!end) errors.to = RANGE_ERROR_KEYS.format;
  if (start && end) {
    if (dayNumber(start) < dayNumber(MIN_DAY)) {
      errors.from = RANGE_ERROR_KEYS.before_min;
    } else if (dayNumber(end) > dayNumber(todayIst(nowMs)) + MAX_DAYS_AHEAD) {
      errors.to = RANGE_ERROR_KEYS.after_today;
    } else if (dayNumber(end) < dayNumber(start)) {
      errors.to = RANGE_ERROR_KEYS.order;
    } else if (dayNumber(end) - dayNumber(start) + 1 > MAX_DAYS) {
      errors.to = RANGE_ERROR_KEYS.too_long;
    }
  } else if (start && dayNumber(start) < dayNumber(MIN_DAY)) {
    errors.from = RANGE_ERROR_KEYS.before_min;
  }
  if (Object.keys(errors).length > 0 || !start || !end) {
    return { errors, range: null };
  }
  return { errors, range: { from: formatDay(start), to: formatDay(end) } };
}

export function parseBy(value: unknown): By {
  return value === "month" ? "month" : "day";
}

/** The page's state as the address holds it (?from=&to=&by=); missing or odd values are defaults. */
export function readUrlState(
  params: { from?: unknown; to?: unknown; by?: unknown },
  nowMs: number,
): { from: string; to: string; by: By } {
  const fallback = defaultRange(nowMs);
  return {
    from:
      typeof params.from === "string" && params.from
        ? params.from
        : fallback.from,
    to: typeof params.to === "string" && params.to ? params.to : fallback.to,
    by: parseBy(params.by),
  };
}

/** The query parameters of GET /api/ihr/owner/totals/. */
export function buildQuery(input: {
  facility: string;
  from: string;
  to: string;
  by: By;
}): Record<string, string> {
  return {
    facility: input.facility,
    from: input.from,
    to: input.to,
    by: input.by,
  };
}

/** The 400 of the endpoint is `{field: message}`; the page shows each beside its box. */
export function fieldErrorsOf(cause: unknown): {
  from?: string;
  to?: string;
  by?: string;
  facility?: string;
  other?: string;
} {
  const out: ReturnType<typeof fieldErrorsOf> = {};
  if (!cause || typeof cause !== "object") return out;
  const body = cause as Record<string, unknown>;
  for (const key of ["from", "to", "by", "facility"] as const) {
    const value = body[key];
    if (typeof value === "string" && value) out[key] = value;
  }
  if (typeof body.detail === "string" && body.detail) out.other = body.detail;
  return out;
}

// --- the answer ------------------------------------------------------------------------

export interface Totals {
  from: string;
  to: string;
  by: By;
  time_zone: string;
  currency: string;
  encounters: { encounter_class: string; status: string; count: number }[];
  inpatients_now: number;
  beds: { total: number; occupied: number };
  invoices_by_status: { status: string; count: number; amount: string }[];
  collected_by_method: { method: string; count: number; amount: string }[];
  refunded: string;
  outstanding_now: string;
  series: {
    period: string;
    admissions: number;
    discharges: number;
    invoiced: string;
    collected: string;
  }[];
}

/** Visits with these statuses are not visits (Q-K52 A). */
export const NOT_A_VISIT = ["cancelled", "entered_in_error"] as const;

function toDecimal(value: unknown): Decimal | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string" && typeof value !== "number") return null;
  try {
    const d = new Decimal(value);
    return d.isFinite() ? d : null;
  } catch {
    return null;
  }
}

function toCount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Sum of counts; null when the list or any of its values is missing. */
function sumCounts(
  rows: unknown,
  pick: (row: never) => unknown,
): number | null {
  if (!Array.isArray(rows)) return null;
  let total = 0;
  for (const row of rows) {
    const n = toCount(pick(row as never));
    if (n === null) return null;
    total += n;
  }
  return total;
}

/** Sum of amount strings, exactly (decimal.js); null when the list or any value is missing. */
function sumAmounts(
  rows: unknown,
  pick: (row: never) => unknown,
): string | null {
  if (!Array.isArray(rows)) return null;
  let total = new Decimal(0);
  for (const row of rows) {
    const d = toDecimal(pick(row as never));
    if (d === null) return null;
    total = total.plus(d);
  }
  return total.toFixed(2);
}

export type TileKey =
  | "visits"
  | "admissions"
  | "discharges"
  | "inpatients_now"
  | "beds"
  | "invoiced"
  | "collected"
  | "refunded"
  | "outstanding_now";

export interface Tile {
  key: TileKey;
  kind: "count" | "money" | "beds";
  /** a count, or an amount string with two decimals; null when the answer lacks it */
  value: number | string | null;
  /** only for "beds": the total the occupied ones are shown against */
  of?: number | null;
}

/**
 * The nine tiles (design §7, §13.5, Q-K52 A): visits = encounter counts without cancelled and
 * entered-in-error; invoiced and collected = sums of the series; admissions and discharges =
 * sums of the series; the rest as the answer states them. A value the answer lacks is null.
 */
export function tiles(t: Totals): Tile[] {
  const visits = Array.isArray(t.encounters)
    ? sumCounts(
        t.encounters.filter(
          (row) => !(NOT_A_VISIT as readonly string[]).includes(row?.status),
        ),
        (row: { count: unknown }) => row.count,
      )
    : null;
  const occupied = toCount(t.beds?.occupied);
  const total = toCount(t.beds?.total);
  const amount = (value: unknown) => {
    const d = toDecimal(value);
    return d === null ? null : d.toFixed(2);
  };
  return [
    { key: "visits", kind: "count", value: visits },
    {
      key: "admissions",
      kind: "count",
      value: sumCounts(
        t.series,
        (row: { admissions: unknown }) => row.admissions,
      ),
    },
    {
      key: "discharges",
      kind: "count",
      value: sumCounts(
        t.series,
        (row: { discharges: unknown }) => row.discharges,
      ),
    },
    { key: "inpatients_now", kind: "count", value: toCount(t.inpatients_now) },
    { key: "beds", kind: "beds", value: occupied, of: total },
    {
      key: "invoiced",
      kind: "money",
      value: sumAmounts(t.series, (row: { invoiced: unknown }) => row.invoiced),
    },
    {
      key: "collected",
      kind: "money",
      value: sumAmounts(
        t.series,
        (row: { collected: unknown }) => row.collected,
      ),
    },
    { key: "refunded", kind: "money", value: amount(t.refunded) },
    { key: "outstanding_now", kind: "money", value: amount(t.outstanding_now) },
  ];
}

/** The translation key of a tile's label. */
export function tileLabelKey(key: TileKey): string {
  return `ihr_totals_tile__${key}`;
}

/** Visits by class and status for the table: counts per class, then per status (stable order). */
export function visitRows(t: Totals): {
  encounter_class: string;
  status: string;
  count: number;
}[] {
  return Array.isArray(t.encounters) ? [...t.encounters] : [];
}

/** "entered_in_error" -> "Entered in error": the words shown for a code we have no string for. */
export function humanize(code: string): string {
  const text = String(code).replace(/_/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "-";
}
