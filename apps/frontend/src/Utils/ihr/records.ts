// IndieHR P1-T11c3 (design-c.md §6, §13.3, §13.4): the rules of MRD's records-access page, the
// patient-page banner and the grant watcher. Pure (no React, no "@/" imports), so
// `node --import tsx --test` runs records.test.ts.
//
// The backend (plugins/ihr_platform/ihr_access/records.py) is the authority: these rules only
// stop the screen from sending a request that is certain to be refused, and decide what the
// screen does when a grant goes away.

/** The reason codes of Q-K36 (RecordsGrant.Reason), in the order the select shows them. */
export const REASON_CODES = [
  "patient_request",
  "legal",
  "insurer",
  "internal_audit",
  "nabh_review",
  "continuity_of_care",
] as const;
export type ReasonCode = (typeof REASON_CODES)[number];

/** Reasons that need a case or claim number (settings.IHR_RECORDS_REFERENCE_REQUIRED default) */
export const REFERENCE_REQUIRED: readonly ReasonCode[] = ["legal", "insurer"];

/** records.REFERENCE_RE: a case or claim number, never free text */
export const REFERENCE_RE = /^[A-Za-z0-9][A-Za-z0-9 ./_:-]{0,99}$/;
export const MAX_REFERENCE = 100;

/** The translation key of a reason's label (deploy/locale/en.json) */
export function reasonLabelKey(code: ReasonCode): string {
  return `ihr_records_reason__${code}`;
}

export function isReasonCode(value: string): value is ReasonCode {
  return (REASON_CODES as readonly string[]).includes(value);
}

export function referenceRequired(reason: string): boolean {
  return (REFERENCE_REQUIRED as readonly string[]).includes(reason);
}

/**
 * One grant as the watcher's poll gets it (GET /api/ihr/records-access/ without names:
 * ihr_access/endpoints.py grant_summary): ids and times only, so the poll shows no patient and
 * writes no audit entry (review BA1).
 */
export interface GrantSummary {
  id: number;
  /** the patient's external id */
  patient: string;
  expires_at: string;
  seconds_left: number;
}

/**
 * One grant with its patient's name (ihr_access/endpoints.py grant_body): the POST 201 body and
 * GET ?names=1, which only the records-access page asks for, once on opening it.
 */
export interface Grant extends GrantSummary {
  patient_name: string;
  patient_year_of_birth: number | null;
  reason: string;
  reference: string;
  created_at: string;
}

export interface GrantList<G extends GrantSummary = GrantSummary> {
  results: G[];
  grant_minutes: number;
}

/** The query of the records page's one list with names (GET ?names=1). */
export const NAMES_QUERY = { names: "1" } as const;

/**
 * The grants the records page shows: the watcher's live list (it decides which grants stand),
 * each with the details the page's own ?names=1 answer gave for it, or null while that answer
 * has not come (the row shows "…" until it does).
 */
export function withDetails(
  live: readonly GrantSummary[],
  details: readonly Grant[] | undefined,
): { grant: GrantSummary; details: Grant | null }[] {
  const byId = new Map((details ?? []).map((g) => [g.id, g]));
  return live.map((grant) => ({ grant, details: byId.get(grant.id) ?? null }));
}

/** The set of live grants as a key (sorted ids): the records page asks for names once per set. */
export function detailsKey(live: readonly GrantSummary[]): string {
  return live
    .map((g) => g.id)
    .sort((a, b) => a - b)
    .join(",");
}

export interface GrantRequest {
  patient: string;
  reason: string;
  reference: string;
}

export type FormErrors = Partial<
  Record<"patient" | "reason" | "reference", string>
>;
/** Keys of our own strings (deploy/locale/en.json) for the client's own errors */
export const FORM_ERROR_KEYS = {
  patient: "ihr_records_error_patient",
  reason: "ihr_records_error_reason",
  referenceRequired: "ihr_records_reference_required",
  referenceInvalid: "ihr_records_reference_invalid",
} as const;

/**
 * The client's check before a POST (the backend repeats every rule). An empty result means the
 * request may be sent; any key means a field error and NO request. The reference is trimmed, as
 * the backend does.
 */
export function validateRequest(input: {
  patient: string | undefined;
  reason: string;
  reference: string;
}): { errors: FormErrors; request: GrantRequest | null } {
  const errors: FormErrors = {};
  const patient = (input.patient ?? "").trim();
  const reference = input.reference.trim();
  if (!patient) errors.patient = FORM_ERROR_KEYS.patient;
  if (!isReasonCode(input.reason)) errors.reason = FORM_ERROR_KEYS.reason;
  if (reference) {
    if (!REFERENCE_RE.test(reference))
      errors.reference = FORM_ERROR_KEYS.referenceInvalid;
  } else if (referenceRequired(input.reason)) {
    errors.reference = FORM_ERROR_KEYS.referenceRequired;
  }
  if (Object.keys(errors).length > 0) return { errors, request: null };
  return { errors, request: { patient, reason: input.reason, reference } };
}

/**
 * The patient id the POST may carry. A phone search answers `partial: true` rows whose `id` is
 * only a partial id (SS2, FF3): a grant is never asked for one. Only a full patient body that
 * CARE loaded for the page (no `partial`, an id) qualifies.
 */
export function patientIdForGrant(
  candidate: { id?: string; partial?: boolean } | null | undefined,
): string | undefined {
  if (!candidate || candidate.partial === true) return undefined;
  const id = candidate.id;
  return typeof id === "string" && id.length > 0 ? id : undefined;
}

/** The ?patient= value of the records page: a UUID, or nothing. */
export function patientFromQuery(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const text = value.trim();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    text,
  )
    ? text.toLowerCase()
    : undefined;
}

export type ServerField = "reference" | "reason" | "form";

/** Which field a backend 400 message (`{detail}`) belongs beside. */
export function serverErrorField(detail: string): ServerField {
  if (/reference/i.test(detail)) return "reference";
  if (/reason/i.test(detail)) return "reason";
  return "form";
}

/** The `detail` of a 400, from the cause an HTTPError carries; undefined when there is none. */
export function detailOf(cause: unknown): string | undefined {
  if (cause && typeof cause === "object" && "detail" in cause) {
    const detail = (cause as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return undefined;
}

/** "23:41": minutes and seconds left, never negative. An hour or more shows "1:05:09". */
export function countdownText(secondsLeft: number): string {
  const total = Math.max(
    0,
    Math.floor(Number.isFinite(secondsLeft) ? secondsLeft : 0),
  );
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  return hours > 0
    ? `${hours}:${two(minutes)}:${two(seconds)}`
    : `${two(minutes)}:${two(seconds)}`;
}

/**
 * Seconds left of a grant at `nowMs`, counted from the `seconds_left` the backend gave when the
 * list was fetched at `fetchedAtMs` (never from the browser's idea of the expiry time: its clock
 * may be wrong).
 */
export function secondsLeftAt(
  grant: Pick<Grant, "seconds_left">,
  fetchedAtMs: number,
  nowMs: number,
): number {
  const passed = Math.max(0, Math.floor((nowMs - fetchedAtMs) / 1000));
  return Math.max(0, grant.seconds_left - passed);
}

/** The moment (ms) the first of these grants runs out, or null when there is none. */
export function earliestExpiryMs(
  grants: readonly Pick<Grant, "seconds_left">[],
  fetchedAtMs: number,
): number | null {
  if (grants.length === 0) return null;
  return fetchedAtMs + Math.min(...grants.map((g) => g.seconds_left)) * 1000;
}

/** The notice above the Ask button: the length comes from the backend (SS7), never hard-coded. */
export function noticeMinutes(grantMinutes: number | undefined): number | null {
  return typeof grantMinutes === "number" &&
    Number.isFinite(grantMinutes) &&
    grantMinutes > 0
    ? Math.round(grantMinutes)
    : null;
}

/** "16-11-2026 14:05": a moment in India time (IST, 24-hour), as the grants list shows it. */
export function formatStarted(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "-";
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type: string) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${get("day")}-${get("month")}-${get("year")} ${get("hour")}:${get("minute")}`;
}

// --- the grant watcher (design §13.3) -----------------------------------------------------

/**
 * A GET that fails while a grant is held: after this many failures IN A ROW the screen stops
 * showing records (fail closed). Decision (P1-T11c3): 2, i.e. about a minute at the 30-second
 * cadence. One failure is a blip and must not throw an MRD clerk out of a record they read;
 * two in a row means the server cannot be asked, so nobody can say the grant still stands,
 * and the screen would otherwise keep showing a record whose grant an administrator may have
 * ended. The grant itself is NOT ended (the server still holds it): the screen clears its
 * cache and goes to the records page, and the next successful GET lists the grant again so the
 * clerk can open the patient anew. Pure rule below, tested in records.test.ts.
 *
 * "Held" also covers a chart the page shows in CARE's own view to a restricted records user
 * (`openCharts`, the patients whose gate decided "care"): for them that view exists only under
 * a grant, so it counts even when no list has ever named the grant (a page loaded straight on
 * the chart whose first look fails, review F2).
 */
export const MAX_FAILURES_WHILE_HELD = 2;

/** How long one look (GET) may take before it counts as failed (review F4). */
export const LOOK_TIMEOUT_MS = 10_000;

export interface HeldGrant {
  id: number;
  /** the patient's external id */
  patient: string;
  /** when the grant runs out, in this browser's clock, counted from seconds_left */
  expiresAtMs: number;
  /**
   * set for a grant taken from the POST 201 body (`noted`): when it was noted. A list whose GET
   * was sent before that moment cannot know the grant, so it never drops it (review F2).
   */
  notedAtMs?: number;
}

export interface WatchState {
  held: readonly HeldGrant[];
  failures: number;
  /** a list has been had at least once */
  confirmed: boolean;
  /** a refusal (403) was seen before any list: it counts once until the next answer */
  refusedUnconfirmed: boolean;
}

export const WATCH_START: WatchState = {
  held: [],
  failures: 0,
  confirmed: false,
  refusedUnconfirmed: false,
};

export type WatchEvent =
  | {
      type: "listed";
      grants: readonly GrantSummary[];
      atMs: number;
      /** when this GET was sent (a grant noted after that is kept); defaults to atMs */
      sentAtMs?: number;
      /** patients whose chart is open in CARE's view for a restricted records user */
      openCharts?: readonly string[];
    }
  | { type: "noted"; grant: GrantSummary; atMs: number }
  | { type: "failed"; openCharts?: readonly string[] }
  | { type: "refused"; openCharts?: readonly string[] }
  | { type: "tick"; atMs: number };

/**
 * What the screen must do now: nothing; "ended" (a grant it held is gone or ran out, or a chart
 * is open that no live grant covers: clear every cached answer, say so, go to the records page);
 * "unconfirmed" (the list could not be had twice in a row while a grant was held or a chart
 * open: the same, with its own words).
 */
export type WatchAction = "none" | "ended" | "unconfirmed";

function heldFrom(grant: GrantSummary, atMs: number): HeldGrant {
  return {
    id: grant.id,
    patient: grant.patient,
    expiresAtMs: atMs + grant.seconds_left * 1000,
  };
}

export function watchStep(
  state: WatchState,
  event: WatchEvent,
): { state: WatchState; action: WatchAction } {
  switch (event.type) {
    case "listed": {
      const sentAtMs = event.sentAtMs ?? event.atMs;
      const listed = event.grants
        .filter((g) => g.seconds_left > 0)
        .map((g) => heldFrom(g, event.atMs));
      const ids = new Set(listed.map((g) => g.id));
      // a grant noted after this GET was sent: the list could not know it yet
      const kept = state.held.filter(
        (g) =>
          !ids.has(g.id) &&
          g.notedAtMs !== undefined &&
          g.notedAtMs >= sentAtMs &&
          g.expiresAtMs > event.atMs,
      );
      const held = [...listed, ...kept];
      const now = new Set(held.map((g) => g.id));
      const lost = state.held.some((g) => !now.has(g.id));
      const covered = new Set(held.map((g) => g.patient));
      const uncovered = (event.openCharts ?? []).some((p) => !covered.has(p));
      return {
        state: {
          held,
          failures: 0,
          confirmed: true,
          refusedUnconfirmed: false,
        },
        action: lost || uncovered ? "ended" : "none",
      };
    }
    case "noted": {
      if (event.grant.seconds_left <= 0) return { state, action: "none" };
      const noted = {
        ...heldFrom(event.grant, event.atMs),
        notedAtMs: event.atMs,
      };
      return {
        state: {
          ...state,
          held: [...state.held.filter((g) => g.id !== noted.id), noted],
        },
        action: "none",
      };
    }
    case "failed":
    case "refused": {
      const charts = (event.openCharts ?? []).length > 0;
      // a refusal counts only before any list, and once until the next answer
      if (
        event.type === "refused" &&
        (state.confirmed || state.refusedUnconfirmed)
      ) {
        return { state, action: "none" };
      }
      const failures = state.failures + 1;
      const next = {
        ...state,
        failures,
        refusedUnconfirmed: event.type === "refused" ? true : false,
      };
      if (
        (state.held.length > 0 || charts) &&
        failures >= MAX_FAILURES_WHILE_HELD
      ) {
        return {
          state: { ...next, held: [], failures: 0 },
          action: "unconfirmed",
        };
      }
      return { state: next, action: "none" };
    }
    case "tick": {
      const still = state.held.filter((g) => g.expiresAtMs > event.atMs);
      if (still.length === state.held.length) {
        return { state, action: "none" };
      }
      return {
        state: { ...state, held: still },
        action: "ended",
      };
    }
  }
}

/**
 * Looks one at a time, none lost (reviews F3, TS4). A look asked for while another runs is not
 * dropped: one more look is sent when the running one ends, and every caller in between gets
 * that later look's promise. So a look asked for after a DELETE or a POST always gets an answer
 * whose GET was sent after it.
 */
export function serialLooks(look: () => Promise<void>): () => Promise<void> {
  let running: Promise<void> | null = null;
  let queued: Promise<void> | null = null;
  const refetch = (): Promise<void> => {
    if (running) {
      queued ??= running.then(() => {
        queued = null;
        return refetch();
      });
      return queued;
    }
    running = look()
      .catch(() => undefined)
      .finally(() => {
        running = null;
      });
    return running;
  };
  return refetch;
}

/**
 * One look with a time limit (review F4): `fetcher` gets a signal that is aborted after `ms`,
 * or when `outer` is aborted (the watcher unmounts); the promise rejects then even if the
 * fetcher ignores its signal, so a hung GET counts as a failure and never blocks later looks.
 */
export function timedLook<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  ms: number,
  outer?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const stop = () => controller.abort();
  outer?.addEventListener("abort", stop);
  const limit = new Promise<never>((_, reject) => {
    const fail = () => reject(new Error("look timed out or was stopped"));
    if (controller.signal.aborted || outer?.aborted) {
      fail();
      return;
    }
    controller.signal.addEventListener("abort", fail);
    timer = setTimeout(stop, ms);
  });
  return Promise.race([fetcher(controller.signal), limit]).finally(() => {
    clearTimeout(timer);
    outer?.removeEventListener("abort", stop);
  });
}

/** Whether this status of a failed request means "refused": 403 is the one that asks the watcher to look. */
export function isRefusal(status: unknown): boolean {
  return status === 403;
}

/** The live grant of this patient in the list, or undefined. */
export function liveGrantFor<G extends GrantSummary>(
  grants: readonly G[] | undefined,
  patientId: string | undefined,
): G | undefined {
  if (!grants || !patientId) return undefined;
  return grants.find((g) => g.patient === patientId);
}
