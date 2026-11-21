// IndieHR P1-T24b (docs/reviews/p1-t24/design.md "T24b as decided", attack.md "Decision"): the
// staff "Sign-in" card's rules. Pure (no React, no "@/" imports), so `node --import tsx --test`
// runs staffAccess.test.ts.
//
// The backend (plugins/ihr_platform/ihr_identity/staff_access.py) is the authority: it says
// which actions may be offered (`offers`), and refuses any other. This file only turns its
// answer into words and buttons:
// - no button without an offer, and the card is hidden when there are none (G2, A5);
// - a problem is shown as a fixed phrase chosen by its CODE; the worker's raw text never
//   reaches the page (A4), and an unknown code is the generic failure;
// - times are DD-MM-YYYY HH:MM in India time, computed here, never from the browser's zone.

export type StaffOffer =
  "resend-invitation" | "cancel-invitation" | "reset-sign-in";

/** The order buttons are drawn in (also: the only actions this build knows). */
const OFFER_ORDER: readonly StaffOffer[] = [
  "resend-invitation",
  "cancel-invitation",
  "reset-sign-in",
];

export interface StaffInvitation {
  status: string;
  kind: string;
  sent_at: string | null;
  expires_at: string | null;
  /** a fixed code from invitations.problem_code, or null */
  problem: string | null;
}

export interface StaffRequestRow {
  action: string;
  status: string;
  requested_at: string;
  problem: string | null;
}

/** GET /api/ihr/staff/<username>/access/ */
export interface StaffAccess {
  active: boolean;
  /** active, awaiting-setup, queued, sent, failed, cancelled, expired or none */
  state: string;
  offers: string[];
  invitation: StaffInvitation | null;
  requests: StaffRequestRow[];
}

/** 202 of POST /api/ihr/staff/<username>/<action>/ */
export interface StaffActionResult {
  id: string;
  status: string;
}

// --- who asks, and whether the card shows ------------------------------------------------------

/**
 * Whether the page asks the backend about this person at all. Never for oneself or a service
 * account, never for a restricted role, never while the mode is unknown (fail closed). The
 * backend still refuses everyone who may not add staff (a 403 hides the card).
 */
export function wantsCard(input: {
  mode: "pending" | "care" | "restricted";
  isOwn: boolean;
  isServiceAccount: boolean;
  hasUsername: boolean;
}): boolean {
  return (
    input.mode === "care" &&
    !input.isOwn &&
    !input.isServiceAccount &&
    input.hasUsername
  );
}

/**
 * The card shows when the backend offered something. Once we asked for an action it stays, so
 * the person who asked sees what became of it (the offers may then be empty: "being sent").
 */
export function cardVisible(
  answer: StaffAccess | undefined,
  asked: boolean,
): boolean {
  if (!answer) return false;
  return asked || answer.offers.length > 0;
}

export function offerButtons(answer: StaffAccess | undefined): StaffOffer[] {
  if (!answer) return [];
  const offered = new Set(answer.offers);
  return OFFER_ORDER.filter((action) => offered.has(action));
}

/** A plain confirm dialog before these two (Q-K113 B); sending again asks nothing. */
export function confirmNeeded(action: StaffOffer): boolean {
  return action === "reset-sign-in" || action === "cancel-invitation";
}

// --- words ---------------------------------------------------------------------------------------

const STATE_KEYS: Record<string, string> = {
  active: "ihr_staff_state_active",
  "awaiting-setup": "ihr_staff_state_awaiting_setup",
  queued: "ihr_staff_state_queued",
  sent: "ihr_staff_state_sent",
  failed: "ihr_staff_state_failed",
  cancelled: "ihr_staff_state_cancelled",
  expired: "ihr_staff_state_expired",
  none: "ihr_staff_state_none",
};

/** The status line: a translation key and its parameters. Unknown states are the neutral line. */
export function stateLine(answer: StaffAccess): {
  key: string;
  params?: Record<string, string>;
} {
  const known = Object.prototype.hasOwnProperty.call(STATE_KEYS, answer.state);
  const key = known ? STATE_KEYS[answer.state] : STATE_KEYS.none;
  if (key === STATE_KEYS.sent) {
    const sent = formatIst(answer.invitation?.sent_at);
    const expires = formatIst(answer.invitation?.expires_at);
    if (sent && expires) return { key, params: { sent, expires } };
    return { key: "ihr_staff_state_sent_plain" };
  }
  return { key };
}

/** invitations.PROBLEM_CODES (plus `failed`): one fixed phrase each. */
const PROBLEM_KEYS: Record<string, string> = {
  "hospital-not-live": "ihr_staff_problem_hospital_not_live",
  "waiting-for-setup": "ihr_staff_problem_waiting_for_setup",
  "other-account-not-set-up": "ihr_staff_problem_other_account_not_set_up",
  "user-gone": "ihr_staff_problem_user_gone",
  "account-gone": "ihr_staff_problem_account_gone",
  "already-active": "ihr_staff_problem_already_active",
  "already-set-up": "ihr_staff_problem_already_set_up",
  "linked-elsewhere": "ihr_staff_problem_linked_elsewhere",
  "no-invitation": "ihr_staff_problem_no_invitation",
  "not-active": "ihr_staff_problem_not_active",
  "not-replaced": "ihr_staff_problem_not_replaced",
  protected: "ihr_staff_problem_protected",
  "try-later": "ihr_staff_problem_try_later",
  "not-allowed": "ihr_staff_problem_not_allowed",
  failed: "ihr_staff_problem_failed",
};

/** null when there is no problem; any code this build does not know is the generic failure. */
export function problemKey(code: string | null | undefined): string | null {
  if (!code) return null;
  return Object.prototype.hasOwnProperty.call(PROBLEM_KEYS, code)
    ? PROBLEM_KEYS[code]
    : PROBLEM_KEYS.failed;
}

const ACTION_KEYS: Record<string, string> = {
  reset_sign_in: "ihr_staff_reset",
  resend_invitation: "ihr_staff_resend",
  cancel_invitation: "ihr_staff_cancel",
};

export function actionLabelKey(action: string): string {
  return Object.prototype.hasOwnProperty.call(ACTION_KEYS, action)
    ? ACTION_KEYS[action]
    : "ihr_staff_action_other";
}

const REQUEST_STATUS_KEYS: Record<string, string> = {
  queued: "ihr_staff_request_queued",
  running: "ihr_staff_request_running",
  done: "ihr_staff_request_done",
  failed: "ihr_staff_request_failed",
};

export function requestStatusKey(status: string): string {
  return Object.prototype.hasOwnProperty.call(REQUEST_STATUS_KEYS, status)
    ? REQUEST_STATUS_KEYS[status]
    : "ihr_staff_request_other";
}

/** The buttons' own labels and the notices shown after an action was asked. */
export const OFFER_LABEL_KEYS: Record<StaffOffer, string> = {
  "reset-sign-in": "ihr_staff_reset",
  "resend-invitation": "ihr_staff_resend",
  "cancel-invitation": "ihr_staff_cancel",
};

export const OFFER_NOTICE_KEYS: Record<StaffOffer, string> = {
  "reset-sign-in": "ihr_staff_notice_reset",
  "resend-invitation": "ihr_staff_notice_resend",
  "cancel-invitation": "ihr_staff_notice_cancel",
};

// --- time ----------------------------------------------------------------------------------------

const IST_OFFSET_MS = 330 * 60_000;

/** "20-11-2026 14:05 IST" from an ISO instant; "" for nothing or something that is not a date. */
export function formatIst(iso: string | null | undefined): string {
  if (!iso) return "";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const d = new Date(ms + IST_OFFSET_MS);
  const two = (n: number) => String(n).padStart(2, "0");
  return (
    `${two(d.getUTCDate())}-${two(d.getUTCMonth() + 1)}-${d.getUTCFullYear()} ` +
    `${two(d.getUTCHours())}:${two(d.getUTCMinutes())} IST`
  );
}

// --- polling -------------------------------------------------------------------------------------

export const POLL_MS = 10_000;
export const MAX_POLLS = 6;

/**
 * How long to wait before asking again: while the invitation is being sent or our request waits
 * or runs, every 10 s, at most 6 times; otherwise never.
 */
export function pollDelay(
  answer: StaffAccess | undefined,
  polls: number,
): number | false {
  if (!answer || polls >= MAX_POLLS) return false;
  const waiting =
    answer.state === "queued" ||
    answer.requests.some(
      (row) => row.status === "queued" || row.status === "running",
    );
  return waiting ? POLL_MS : false;
}

// --- a refusal's phrase ---------------------------------------------------------------------------

/** staff_access.RefusalError: the 400's `code` -> our phrase. Anything else is the generic phrase. */
const REFUSAL_KEYS: Record<string, string> = {
  own: "ihr_staff_error_own",
  "not-active-yet": "ihr_staff_error_not_active",
  "reset-recent": "ihr_staff_error_reset_recent",
  "protected-recent": "ihr_staff_error_protected_recent",
  "already-active": "ihr_staff_error_already_active",
  "being-sent": "ihr_staff_error_being_sent",
  "no-invitation": "ihr_staff_error_no_invitation",
  "already-asked": "ihr_staff_error_already_asked",
};

/**
 * The translation key for a 400's body (`{code, detail}`). The backend's own English `detail`
 * is never shown, so the Hindi page stays in Hindi (review W3); no code, or one this build does
 * not know, is the generic phrase.
 */
export function refusalKey(cause: unknown): string {
  const generic = "ihr_staff_error_request";
  if (!cause || typeof cause !== "object" || Array.isArray(cause))
    return generic;
  const code = (cause as Record<string, unknown>).code;
  if (typeof code !== "string") return generic;
  return Object.prototype.hasOwnProperty.call(REFUSAL_KEYS, code)
    ? REFUSAL_KEYS[code]
    : generic;
}

/**
 * Whether the invitation's problem line is shown in this state (review K7): while the invitation
 * is open or failed. A cancelled or expired one, or a person with none, shows no old problem.
 */
export function problemShown(state: string): boolean {
  return state === "queued" || state === "sent" || state === "failed";
}
