// IndieHR P1-T24c (docs/reviews/p1-t24/design.md "T24c as decided", attack.md "Decision"): the
// reference-list updates page's rules. Pure (no React, no "@/" imports), so
// `node --import tsx --test` runs packs.test.ts.
//
// The backend (plugins/ihr_platform/ihr_refdata/api.py) is the authority. This file only turns
// its answers into words:
// - every POST answer is a FIXED phrase chosen by status and `code` (the table in the design);
//   the server's own `detail` and `reason` text never reaches the page, as on the staff card;
// - "Keep ours" is offered on pending items only (a keep on a kept item is a 404);
// - a hospital-wide item says "whole hospital", a facility's item its facility's name;
// - days are DD-MM-YYYY in India time, computed here, never from the browser's zone.
import { formatIst } from "./staffAccess";

/** api.py: engine.MAX_REASON */
export const MAX_REASON = 300;
/** api.py: PAGE */
export const PAGE_SIZE = 50;

export type PackState = "pending" | "kept";
export type PackAction = "accept" | "keep";

/** One row of GET /api/ihr/packs/updates/ (api.py `_summary`). */
export interface PackSummary {
  id: number;
  pack: string;
  kind: string;
  item: string;
  label: string;
  /** the facility's external id; null for a hospital-wide item */
  facility: string | null;
  facility_name: string | null;
  state: PackState;
  reason: string;
  applied_version: number | null;
  pending_version: number | null;
  changed_at: string;
}

export interface PackList {
  count: number;
  results: PackSummary[];
}

export type PackValue = unknown;

/** One field of the difference: three columns, or (a large field) the changed paths. */
export interface PackField {
  field: string;
  hospital?: PackValue;
  pack?: PackValue;
  previous?: PackValue;
  truncated?: boolean;
  changes?: {
    path: string;
    hospital: PackValue;
    pack: PackValue;
    previous: PackValue;
  }[];
}

/** What holds an item back, as stored by the last apply (counts only). */
export interface PackHeld {
  counts?: Record<string, number>;
  conflict?: string;
  missing_link?: string;
  patients?: number;
  complete?: boolean;
  note?: string;
  checked_at?: string;
}

/** GET /api/ihr/packs/updates/<id>/ */
export interface PackDetail extends PackSummary {
  pending_sha256: string;
  row_sha256: string;
  /** update, create, restore, retire, new-version or status-only */
  effect: string;
  /** the reason that refuses an accept now, or "" */
  blocked: string;
  conflicts: PackHeld;
  fields: PackField[];
  notes?: {
    previous?: string;
    stranded?: { draft_submissions?: number; response_templates?: number };
  };
  /** a POST answer only */
  unchanged?: boolean;
}

export interface PackDecisionBody {
  action: PackAction;
  version: number;
  sha256: string;
  row_sha256: string;
  reason: string;
}

export interface PackHistoryChange {
  item: string;
  facility: string | null;
  change: string;
}

export type PackHistoryBy =
  "start-up" | "operator" | { user: string | null; username: string | null };

/** One row of GET /api/ihr/packs/history/ (api.py HistoryView). */
export interface PackHistoryEntry {
  id: number;
  pack: string;
  version: number | null;
  outcome: string;
  at: string;
  by: PackHistoryBy;
  counts: Record<string, unknown>;
  /** null: an apply from before the change list was kept */
  changes: PackHistoryChange[] | null;
  note?: string;
}

export interface PackHistoryList {
  count: number;
  results: PackHistoryEntry[];
}

// --- words ------------------------------------------------------------------------------------

const EFFECTS: ReadonlySet<string> = new Set([
  "update",
  "create",
  "restore",
  "retire",
  "new-version",
  "status-only",
]);

/** What accepting does, as a translation key. */
export function effectKey(effect: string): string {
  return EFFECTS.has(effect)
    ? `ihr_packs_effect__${effect}`
    : "ihr_packs_effect__other";
}

const WHY: ReadonlySet<string> = new Set([
  "edited",
  "unknown-origin",
  "deleted",
  "conflict",
  "data-check",
  "privacy",
  "counter-restart",
  "missing-link",
]);

/** Why an item waits, as a translation key; null when no reason is stored. */
export function whyKey(reason: string): string | null {
  if (!reason) return null;
  return WHY.has(reason) ? `ihr_packs_why__${reason}` : "ihr_packs_why__other";
}

/** The facility column: its name, "whole hospital" for a hospital-wide item, "-" if unnamed. */
export function facilityText(
  item: Pick<PackSummary, "facility" | "facility_name">,
  wholeHospital: string,
): string {
  if (item.facility === null || item.facility === undefined) {
    return wholeHospital;
  }
  return item.facility_name ? item.facility_name : "-";
}

// --- keep and accept --------------------------------------------------------------------------

/** "Keep ours" only on a pending item: the backend answers 404 for a keep on a kept one. */
export function canKeep(item: Pick<PackSummary, "state">): boolean {
  return item.state === "pending";
}

/** Accepting is refused while a conflict, data check or missing link holds the item. */
export function canAccept(item: Pick<PackDetail, "blocked">): boolean {
  return !item.blocked;
}

export interface ReasonCheck {
  ok: boolean;
  /** the reason as it will be sent: on one line, single spaces */
  text: string;
  /** a translation key when not ok */
  error?: string;
}

/** The backend's rule (engine.normal_reason): one line, 1 to MAX_REASON characters. */
export function reasonCheck(input: string): ReasonCheck {
  const text = input.split(/\s+/).filter(Boolean).join(" ");
  if (!text) return { ok: false, text, error: "ihr_packs_error_reason" };
  if (text.length > MAX_REASON) {
    return { ok: false, text, error: "ihr_packs_error_reason" };
  }
  return { ok: true, text };
}

/** The POST body: what the admin was shown (version and both hashes) and the reason. */
export function decisionBody(
  item: Pick<PackDetail, "pending_version" | "pending_sha256" | "row_sha256">,
  action: PackAction,
  reason: string,
): PackDecisionBody {
  return {
    action,
    version: item.pending_version ?? 0,
    sha256: item.pending_sha256,
    row_sha256: item.row_sha256,
    reason: reasonCheck(reason).text,
  };
}

// --- every answer of POST /api/ihr/packs/updates/<id>/ ------------------------------------------

export type After =
  /** close the sheet and reload the list */
  | "close"
  /** the item is gone: close the sheet and reload the list */
  | "list"
  /** the item changed: reload the open sheet */
  | "detail"
  /** nothing moves; the sheet stays as it is */
  | "stay";

export interface Answer {
  /** a translation key (deploy/locale/en.json) */
  key: string;
  after: After;
  /** a success, not a problem */
  ok: boolean;
}

function bodyOf(body: unknown): Record<string, unknown> {
  return body && typeof body === "object" && !Array.isArray(body)
    ? (body as Record<string, unknown>)
    : {};
}

/**
 * Our session classes send no WWW-Authenticate header (ihr_identity/authentication.py), so DRF
 * answers a signed-out or expired session with a 403 and its own NotAuthenticated text, never a
 * 401 (review P1). That text, and only that, tells it from "not for your role".
 */
const NOT_AUTHENTICATED = "Authentication credentials were not provided.";

function isSignedOut(status: number | undefined, body: unknown): boolean {
  return status === 403 && bodyOf(body).detail === NOT_AUTHENTICATED;
}

/**
 * The status-to-message table (design "T24c as decided"). `status` 200 is a decision made
 * (body.unchanged: the same decision was already recorded); the others are errors, read from
 * the error's body. Only fixed phrases come out. An answer this build does not know is the
 * generic failure.
 */
export function answerOf(
  action: PackAction,
  status: number | undefined,
  body: unknown,
): Answer {
  const b = bodyOf(body);
  const code = typeof b.code === "string" ? b.code : "";
  const save: Answer = {
    key: "ihr_packs_error_save",
    after: "stay",
    ok: false,
  };
  if (status === 200) {
    return {
      key:
        b.unchanged === true
          ? "ihr_packs_done_already"
          : action === "accept"
            ? "ihr_packs_done_accept"
            : "ihr_packs_done_keep",
      after: "close",
      ok: true,
    };
  }
  if (status === 400) {
    // the engine's refusal also carries a `reason`: told apart by its code
    if (code === "refused") return save;
    if (typeof b.reason === "string") {
      return { key: "ihr_packs_error_reason", after: "stay", ok: false };
    }
    return save;
  }
  if (status === 401 || isSignedOut(status, body)) {
    return { key: "ihr_packs_error_signed_out", after: "stay", ok: false };
  }
  if (status === 403) {
    return { key: "ihr_packs_error_forbidden", after: "stay", ok: false };
  }
  if (status === 404) {
    return { key: "ihr_packs_error_gone", after: "list", ok: false };
  }
  if (status === 409) {
    if (code === "not-live") {
      return { key: "ihr_packs_error_not_live", after: "stay", ok: false };
    }
    if (code === "stale" || (code === "" && b.stale === true)) {
      return { key: "ihr_packs_error_stale", after: "detail", ok: false };
    }
    if (code === "blocked" || (code === "" && b.blocked === true)) {
      return { key: "ihr_packs_error_blocked", after: "detail", ok: false };
    }
    if (code === "busy" || (code === "" && b.busy === true)) {
      return { key: "ihr_packs_error_busy", after: "stay", ok: false };
    }
    return save;
  }
  if (status === 429) {
    return code === "rate" || code === "in-flight"
      ? { key: "ihr_packs_error_many", after: "stay", ok: false }
      : save;
  }
  if (status === 503 && code === "unavailable") {
    return { key: "ihr_packs_error_unavailable", after: "stay", ok: false };
  }
  // the release gate (release_gate.py) holds a hospital during a release, a restore or another build
  if (status === 503 && code === "ihr_upgrading") {
    return { key: "ihr_packs_error_upgrading", after: "stay", ok: false };
  }
  return save;
}

/** The message under a list or sheet that could not be loaded. */
export function loadErrorKey(
  status: number | undefined,
  body: unknown,
): string {
  if (status === 401 || isSignedOut(status, body)) {
    return "ihr_packs_error_signed_out";
  }
  if (status === 403) return "ihr_packs_error_forbidden";
  if (status === 503 && bodyOf(body).code === "unavailable") {
    return "ihr_packs_error_unavailable";
  }
  if (status === 503 && bodyOf(body).code === "ihr_upgrading") {
    return "ihr_packs_error_upgrading";
  }
  return "ihr_packs_error_load";
}

/** A retry cannot help a signed-out or not-allowed reader (401, 403). */
export function canRetryLoad(status: number | undefined): boolean {
  return status !== 401 && status !== 403;
}

// --- values and checks ------------------------------------------------------------------------

/** A field's value as text: "-" for nothing, JSON for anything that is not a string. */
export function fieldValue(value: PackValue): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

/** The stored data check as one fixed phrase; null when there is none. */
export function checkLine(
  held: Pick<PackHeld, "patients" | "complete">,
): { key: string; params: Record<string, number> } | null {
  if (typeof held.patients !== "number") return null;
  if (held.complete !== true) {
    return { key: "ihr_packs_check_partial", params: {} };
  }
  if (held.patients > 0) {
    return {
      key: "ihr_packs_check_patients",
      params: { count: held.patients },
    };
  }
  return { key: "ihr_packs_check_clear", params: {} };
}

// --- pages ------------------------------------------------------------------------------------

/**
 * The offset to show when the count has shrunk under it (a decision emptied the last page):
 * the start of the last page that has rows, 0 for none.
 */
export function clampOffset(offset: number, count: number): number {
  if (count <= 0) return 0;
  const last = Math.floor((count - 1) / PAGE_SIZE) * PAGE_SIZE;
  return Math.min(offset, last);
}

export function pageRange(
  offset: number,
  count: number,
): { from: number; to: number; hasPrev: boolean; hasNext: boolean } {
  if (count <= 0) return { from: 0, to: 0, hasPrev: false, hasNext: false };
  const to = Math.min(offset + PAGE_SIZE, count);
  return { from: offset + 1, to, hasPrev: offset > 0, hasNext: to < count };
}

// --- history ----------------------------------------------------------------------------------

/** Who: start-up and operators by a fixed word, staff by their username. */
export function historyBy(
  by: PackHistoryBy | undefined,
): { key: string } | { name: string } {
  if (by === "start-up") return { key: "ihr_packs_by_startup" };
  if (by === "operator") return { key: "ihr_packs_by_operator" };
  if (
    by &&
    typeof by === "object" &&
    typeof by.username === "string" &&
    by.username
  ) {
    return { name: by.username };
  }
  return { key: "ihr_packs_by_unknown" };
}

export interface HistorySummary {
  /** the changes an apply made, as many as the backend listed (at most 500) */
  listed: PackHistoryChange[];
  /** the recorded total of an apply; null when there is none */
  total: number | null;
  /** an apply from before the change list was kept */
  notRecorded: boolean;
  /** a decision's item and the reason the admin gave */
  item?: string;
  reason?: string;
}

export function historySummary(entry: PackHistoryEntry): HistorySummary {
  const counts = entry.counts ?? {};
  if (entry.outcome === "accepted" || entry.outcome === "kept") {
    return {
      listed: [],
      total: null,
      notRecorded: false,
      item: typeof counts.item === "string" ? counts.item : undefined,
      reason: typeof counts.reason === "string" ? counts.reason : undefined,
    };
  }
  if (entry.outcome === "applied") {
    if (entry.changes === null) {
      return { listed: [], total: null, notRecorded: true };
    }
    const total =
      typeof counts.changes_total === "number"
        ? counts.changes_total
        : entry.changes.length;
    return { listed: entry.changes, total, notRecorded: false };
  }
  return { listed: [], total: null, notRecorded: false };
}

/** The words of a change of an apply (a translation key; unknown ones are shown as they are). */
export const CHANGE_WORDS: ReadonlySet<string> = new Set([
  "created",
  "updated",
  "restored",
  "repaired",
  "retired",
  "pending",
  "resolved",
  "adopted",
  "kept",
]);

export const OUTCOME_WORDS: ReadonlySet<string> = new Set([
  "applied",
  "reached",
  "failed",
  "deferred",
  "accepted",
  "kept",
]);

/** DD-MM-YYYY (India time) of an ISO instant; "-" for nothing or something that is no date. */
export function sinceText(iso: string | null | undefined): string {
  const text = formatIst(iso);
  return text ? text.slice(0, 10) : "-";
}

/** The tabs, and the one an address names (anything else is the first). */
export const PACK_TABS = ["pending", "kept", "history"] as const;
export type PackTab = (typeof PACK_TABS)[number];

export function readTab(value: unknown): PackTab {
  return (PACK_TABS as readonly string[]).includes(value as string)
    ? (value as PackTab)
    : "pending";
}
