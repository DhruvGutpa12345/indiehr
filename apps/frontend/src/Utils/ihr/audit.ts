// IndieHR P1-T24a (docs/reviews/p1-t24/design.md §2.1, attack.md "Decision"): the audit trail
// page's rules. Pure (no React, no "@/" imports), so `node --import tsx --test` runs
// audit.test.ts. The backend (plugins/ihr_platform/ihr_audit/views.py) is the authority: the
// checks here only name a mistake without a request, and a refusal's own message is shown.
//
// The API takes days as YYYY-MM-DD (IST days, Q-K116 A); the page shows and keeps DD-MM-YYYY.
// No typed "Who" filter: the actor is `user:<pk>:<username>`, so a person is found by clicking
// the actor of an entry, which filters on that exact actor string.
import { RANGE_ERROR_KEYS, formatDay, parseDay, todayIst } from "./totals";

export const AUDIT_KINDS = ["write", "request", "truncate"] as const;
export const AUDIT_OPS = ["INSERT", "UPDATE", "DELETE", "TRUNCATE"] as const;
/** views.py: `actor[:200]` */
export const MAX_ACTOR = 200;
/** views.py: PAGE */
export const PAGE_SIZE = 50;

export interface AuditPatient {
  /** the patient's external id; null when the lookup could not name them */
  id: string | null;
  name: string | null;
}

/** One row of GET /api/ihr/audit/entries/ (views.py `summary`). */
export interface AuditEntry {
  id: number;
  at: string;
  /** DD-MM-YYYY HH:MM:SS, India time */
  at_ist: string;
  kind: "write" | "truncate" | "request";
  actor: string;
  purpose: string | null;
  grants?: unknown;
  via?: string | null;
  ip?: string | null;
  method?: string | null;
  path?: string | null;
  query?: string | null;
  task?: string | null;
  request_id?: string | null;
  device_tz?: string | null;
  table: string | null;
  op: string | null;
  record: string | null;
  patients: AuditPatient[];
  patients_total: number;
  status?: number | null;
  fields?: string[];
}

export interface AuditList {
  results: AuditEntry[];
  /** the id to pass as `before` for older entries; null at the end */
  next_before: number | null;
}

/** GET /api/ihr/audit/entries/<id>/: the entry with its context and old and new values. */
export interface AuditEntryDetail extends AuditEntry {
  context?: Record<string, unknown>;
  values?: unknown;
}

export interface AuditFingerprints {
  configured?: boolean;
  readable?: boolean;
  accepted?: boolean;
  [key: string]: unknown;
}

/** GET /api/ihr/audit/status/ */
export interface AuditStatus {
  seals: number;
  head: string | null;
  last_sealed_at: string | null;
  unsealed_entries: number;
  oldest_unsealed_at: string | null;
  triggers_ok: boolean;
  trigger_problems: unknown[];
  fingerprints?: AuditFingerprints | null;
}

// --- the address ------------------------------------------------------------------------

/** The filters as the address and the boxes hold them (days in DD-MM-YYYY). */
export interface AuditFilters {
  from: string;
  to: string;
  kind: string;
  op: string;
  /** a patient's external id (set by a link, never typed) */
  patient: string;
  /** an exact actor string, as an entry shows it (set by a link) */
  actor: string;
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isPatientId(value: unknown): value is string {
  return typeof value === "string" && UUID_RE.test(value);
}

function oneOf<T extends string>(list: readonly T[], value: unknown): T | "" {
  return typeof value === "string" &&
    (list as readonly string[]).includes(value)
    ? (value as T)
    : "";
}

/** The page's state as the address holds it; a missing or odd value is the default (today, IST). */
export function readAuditUrl(
  params: Record<string, unknown>,
  nowMs: number,
): AuditFilters {
  const today = formatDay(todayIst(nowMs));
  const text = (key: string) =>
    typeof params[key] === "string" && params[key]
      ? (params[key] as string)
      : "";
  return {
    from: text("from") || today,
    to: text("to") || today,
    kind: oneOf(AUDIT_KINDS, params.kind),
    op: oneOf(AUDIT_OPS, params.op),
    patient: isPatientId(params.patient) ? params.patient : "",
    actor: text("actor").slice(0, MAX_ACTOR),
  };
}

/** The address for a set of filters: no empty key. */
export function auditUrlParams(filters: AuditFilters): Record<string, string> {
  const out: Record<string, string> = { from: filters.from, to: filters.to };
  for (const key of ["kind", "op", "patient", "actor"] as const) {
    if (filters[key]) out[key] = filters[key];
  }
  return out;
}

/** One string per filter set: when it changes, the older pages loaded so far are dropped. */
export function auditFilterKey(filters: AuditFilters): string {
  return JSON.stringify([
    filters.from,
    filters.to,
    filters.kind,
    filters.op,
    filters.patient,
    filters.actor,
  ]);
}

// --- the range --------------------------------------------------------------------------

/** "DD-MM-YYYY" (also D-M-YYYY) as the API's "YYYY-MM-DD", or null when it is not a real day. */
export function toApiDay(text: string): string | null {
  const day = parseDay(text);
  if (!day) return null;
  return `${String(day.year).padStart(4, "0")}-${String(day.month).padStart(2, "0")}-${String(day.day).padStart(2, "0")}`;
}

export interface AuditRangeCheck {
  /** translation keys (deploy/locale/en.json) */
  errors: Partial<Record<"from" | "to", string>>;
  /** the API days, only when there is no error */
  range: { from: string; to: string } | null;
}

/**
 * The same mistakes the backend would refuse (format, a day before 2000, last before first).
 * There is no length limit for the list (the export's 31 days is T24c's).
 */
export function checkAuditRange(from: string, to: string): AuditRangeCheck {
  const errors: AuditRangeCheck["errors"] = {};
  const start = parseDay(from);
  const end = parseDay(to);
  if (!start) errors.from = RANGE_ERROR_KEYS.format;
  if (!end) errors.to = RANGE_ERROR_KEYS.format;
  if (start && start.year < 2000) errors.from = RANGE_ERROR_KEYS.before_min;
  if (start && end && !errors.from) {
    const a = Date.UTC(start.year, start.month - 1, start.day);
    const b = Date.UTC(end.year, end.month - 1, end.day);
    if (b < a) errors.to = RANGE_ERROR_KEYS.order;
  }
  const a = toApiDay(from);
  const b = toApiDay(to);
  if (Object.keys(errors).length > 0 || !a || !b)
    return { errors, range: null };
  return { errors, range: { from: a, to: b } };
}

/**
 * A change of one filter (kind, type of change, who) made while dates are typed in the boxes but
 * not yet submitted: the boxes are checked and travel with the change, so the address change
 * does not throw them away (review W2). `next` is null when the dates are mistaken.
 */
export function withDraftDates(
  filters: AuditFilters,
  from: string,
  to: string,
  change: Partial<AuditFilters>,
): { errors: AuditRangeCheck["errors"]; next: AuditFilters | null } {
  const check = checkAuditRange(from, to);
  if (!check.range) return { errors: check.errors, next: null };
  return { errors: {}, next: { ...filters, from, to, ...change } };
}

/** The query of GET /api/ihr/audit/entries/ (only the filters that are set). */
export function buildAuditQuery(
  filters: AuditFilters,
  range: { from: string; to: string },
  before?: number | null,
): Record<string, string> {
  const out: Record<string, string> = { from: range.from, to: range.to };
  for (const key of ["kind", "op", "patient"] as const) {
    if (filters[key]) out[key] = filters[key];
  }
  // a link's actor is one whole actor string: the exact match, never the prefix one (review A3)
  if (filters.actor) out.actor_exact = filters.actor;
  if (before !== undefined && before !== null) out.before = String(before);
  return out;
}

// --- a row ------------------------------------------------------------------------------

export interface ActorParts {
  kind: "user" | "service" | "patient" | "system" | "anonymous" | "other";
  /** what to show */
  label: string;
  /** the whole actor string: the exact-actor filter sends it */
  actor: string;
}

/** `user:<pk>:<username>` shows the username; any other actor is shown as it is. */
export function actorParts(actor: string): ActorParts {
  const m = /^(user|service):\d+:(.+)$/.exec(actor);
  if (m) return { kind: m[1] as "user" | "service", label: m[2], actor };
  if (actor === "anonymous") return { kind: "anonymous", label: actor, actor };
  if (actor.startsWith("patient:"))
    return { kind: "patient", label: actor, actor };
  if (actor.startsWith("system:"))
    return { kind: "system", label: actor, actor };
  return { kind: "other", label: actor, actor };
}

export type What =
  | {
      type: "request";
      purpose: string | null;
      method: string | null;
      path: string | null;
    }
  | { type: "change"; table: string | null; op: string | null };

export function whatOf(entry: AuditEntry): What {
  if (entry.kind === "request") {
    return {
      type: "request",
      purpose: entry.purpose ?? null,
      method: entry.method ?? null,
      path: entry.path ?? null,
    };
  }
  return { type: "change", table: entry.table ?? null, op: entry.op ?? null };
}

export type Result =
  | { type: "status"; status: number }
  | { type: "fields"; fields: string[] }
  | { type: "none" };

export function resultOf(entry: AuditEntry): Result {
  if (entry.kind === "request") {
    return typeof entry.status === "number"
      ? { type: "status", status: entry.status }
      : { type: "none" };
  }
  if (
    entry.op === "UPDATE" &&
    Array.isArray(entry.fields) &&
    entry.fields.length
  ) {
    return { type: "fields", fields: entry.fields };
  }
  return { type: "none" };
}

/** The patients named in a row, and how many more the entry holds (the list names at most 20). */
export function patientsShown(entry: AuditEntry): {
  shown: AuditPatient[];
  more: number;
} {
  const shown = Array.isArray(entry.patients) ? entry.patients : [];
  const total =
    typeof entry.patients_total === "number" ? entry.patients_total : 0;
  return { shown, more: Math.max(0, total - shown.length) };
}

// --- the sheet's values ----------------------------------------------------------------

export interface ValueRow {
  field: string;
  /** an update: old and new; an insert or a delete: one value */
  old?: string;
  new?: string;
  value?: string;
}

function show(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

/**
 * Old and new values of one entry, by field name. An update's values are `{field: [old, new]}`;
 * a hidden secret is the text "(hidden)" (shown for both). An insert or a delete holds
 * `{field: value}`. Anything else (no values) is no rows.
 */
export function valueRows(
  op: string | null | undefined,
  values: unknown,
  kind?: string,
): ValueRow[] {
  // a request entry's data is its result ({status, patients_total}), not a record's values
  if (kind === "request") return [];
  if (!values || typeof values !== "object" || Array.isArray(values)) return [];
  const rows: ValueRow[] = [];
  for (const field of Object.keys(values).sort()) {
    const raw = (values as Record<string, unknown>)[field];
    if (op === "UPDATE") {
      if (Array.isArray(raw) && raw.length === 2) {
        rows.push({ field, old: show(raw[0]), new: show(raw[1]) });
      } else {
        rows.push({ field, old: show(raw), new: show(raw) });
      }
    } else {
      rows.push({ field, value: show(raw) });
    }
  }
  return rows;
}

// --- the status strip -------------------------------------------------------------------

export type FingerprintState =
  "locked" | "problem" | "unreadable" | "not_configured" | "unknown";

/** The outside fingerprint bucket's answer (anchor.lock_status) as one word. */
export function fingerprintState(
  fp: AuditFingerprints | null | undefined,
): FingerprintState {
  if (!fp || typeof fp !== "object") return "unknown";
  if (fp.configured === false) return "not_configured";
  if (fp.configured !== true) return "unknown";
  if (fp.readable === false) return "unreadable";
  if (fp.accepted === true) return "locked";
  if (fp.accepted === false) return "problem";
  return "unknown";
}

export interface StatusStrip {
  /** DD-MM-YYYY HH:MM:SS, India time; null before the first seal */
  lastSealed: string | null;
  /** entries not yet sealed; null when the answer lacks it (never shown as 0) */
  unsealed: number | null;
  triggersOk: boolean;
  triggerProblems: number;
  fingerprints: FingerprintState;
}

export function statusStrip(status: AuditStatus): StatusStrip {
  const problems = Array.isArray(status.trigger_problems)
    ? status.trigger_problems.length
    : 0;
  return {
    lastSealed:
      typeof status.last_sealed_at === "string" && status.last_sealed_at
        ? status.last_sealed_at
        : null,
    unsealed:
      typeof status.unsealed_entries === "number"
        ? status.unsealed_entries
        : null,
    // fail closed: a missing flag, or any listed problem, is not "all in place"
    triggersOk: status.triggers_ok === true && problems === 0,
    triggerProblems: problems,
    fingerprints: fingerprintState(status.fingerprints),
  };
}
