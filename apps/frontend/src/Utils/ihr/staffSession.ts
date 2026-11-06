// IndieHR (P1-T4b, ADR-009): staff sign-in is run by the backend. The browser holds only an
// HttpOnly cookie, so this file never touches a token. It adds the two headers the backend
// expects, measures idle time from real input (shared by this hospital's tabs), and clears
// everything cached in the browser when a session ends (Q-T9).
import FiltersCache from "@/Utils/FiltersCache";
import ViewCache from "@/Utils/ViewCache";
import {
  idleSecondsSince,
  idleSettingsFrom,
  isOwnApi,
  safeNext,
} from "@/Utils/ihr/idleRules";
import { invalidateAllPaymentReconcilationLocationCaches } from "@/atoms/paymentReconcilationLocationAtom";
import { clearQueuePractitionerCache } from "@/atoms/queuePractitionerAtom";

export const SIGN_IN_URL = "/api/ihr/auth/login/";
const SIGN_OUT_URL = "/api/ihr/auth/logout/";
const SESSION_URL = "/api/ihr/auth/session/";
const LAST_ACTIVITY_KEY = "ihr_last_activity";
// Set while a sign-out has not been confirmed by the backend: the app stays signed out here
// (and keeps retrying) instead of quietly carrying on with the still-valid session.
const SIGN_OUT_PENDING_KEY = "ihr_sign_out_pending";
const CHANNEL = "ihr-auth";
// CARE's old staff tokens, cleared wherever they are left over
const LEGACY_KEYS = [
  "care_access_token",
  "care_refresh_token",
  "mfa_temp_token",
];
const PERSISTED_QUERIES_KEY = "REACT_QUERY_OFFLINE_CACHE";
// Cached per-person data kept outside react-query
const CACHED_PREFIXES = ["pharmacy-dispense-service--"];

export type EndReason =
  "idle" | "signed_out" | "signed_out_here_only" | "session_ended";

export function idleSettings() {
  return idleSettingsFrom(
    (window as unknown as { __IHR_AUTH__?: unknown }).__IHR_AUTH__,
  );
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function storedActivity() {
  try {
    const value = storage()?.getItem(LAST_ACTIVITY_KEY);
    return value === null || value === undefined ? null : Number(value);
  } catch {
    return null;
  }
}

// This tab's own last input: the fallback when shared storage is missing or unreadable, so
// "unknown" never counts as "just now".
let tabLastInput = Date.now();
let lastWrite = 0;

/** Real input happened (keyboard, mouse, touch). Written at most every 15 s. */
export function recordActivity(force = false) {
  const now = Date.now();
  tabLastInput = now;
  if (!force && now - lastWrite < 15_000) return;
  lastWrite = now;
  try {
    storage()?.setItem(LAST_ACTIVITY_KEY, String(now));
  } catch {
    // storage full or blocked: this tab's own time is used instead
  }
}

export function idleSeconds() {
  const shared = storedActivity();
  const last =
    shared !== null && Number.isFinite(shared)
      ? Math.max(shared, tabLastInput)
      : tabLastInput;
  return idleSecondsSince(last, Date.now());
}

/** The headers every request to our API carries: CSRF guard and idle time. */
export function addSessionHeaders(url: string, headers: Headers) {
  if (!isOwnApi(url, window.location.origin)) return;
  headers.set("X-IHR-CSRF", "1");
  headers.set("X-IHR-Idle", String(idleSeconds()));
}

/** Go to the backend, which signs in through Keycloak and comes back to ``next``. */
export function startSignIn(next?: string | null) {
  try {
    storage()?.removeItem(SIGN_OUT_PENDING_KEY);
  } catch {
    // storage blocked
  }
  const target = safeNext(next ?? null, window.location.origin);
  window.location.assign(
    `${SIGN_IN_URL}?${new URLSearchParams({ next: target })}`,
  );
}

export function forgetLegacyTokens() {
  try {
    for (const key of LEGACY_KEYS) storage()?.removeItem(key);
  } catch {
    // storage blocked
  }
}

/** Everything cached for the signed-in person in this browser (not the patient portal's). */
export function clearStaffStorage() {
  try {
    const store = storage();
    if (store) {
      for (const key of [...LEGACY_KEYS, PERSISTED_QUERIES_KEY]) {
        store.removeItem(key);
      }
      for (const key of Object.keys(store)) {
        if (CACHED_PREFIXES.some((prefix) => key.startsWith(prefix))) {
          store.removeItem(key);
        }
      }
    }
    sessionStorage.clear();
  } catch {
    // storage blocked: nothing was stored either
  }
  ViewCache.invalidateAll();
  FiltersCache.invalidateAll();
  invalidateAllPaymentReconcilationLocationCaches();
  clearQueuePractitionerCache();
}

// Whether a staff person is signed in in this tab (set by AuthUserProvider), so a refused
// request on a patient-portal page never tears down a staff session.
let staffSignedIn = false;
export function setStaffSignedIn(value: boolean) {
  staffSignedIn = value;
}

/** The server says the session has ended (idle, sign-out elsewhere, removal): drop everything
 * this browser holds and go to the sign-in page, keeping where the person was. False when
 * no staff person is signed in here (the caller handles it as before). */
export function endExpiredSession(clearQueries: () => Promise<void>) {
  if (!staffSignedIn) return false;
  staffSignedIn = false;
  const here = window.location.pathname + window.location.search;
  const params = new URLSearchParams({ reason: "session_ended" });
  if (!window.location.pathname.startsWith("/login"))
    params.set("redirect", here);
  clearQueries().finally(() => {
    clearStaffStorage();
    announceSignOut("session_ended");
    window.location.replace(`/login?${params}`);
  });
  return true;
}

export function signOutPending() {
  try {
    return storage()?.getItem(SIGN_OUT_PENDING_KEY) === "1";
  } catch {
    return false;
  }
}

/** Ask the backend to end the session (and single sign-on everywhere). Until it confirms,
 * this browser stays signed out of the app and the request is retried on every load.
 * Returns whether Keycloak confirmed the sign-out everywhere, or null if the backend could
 * not be reached. */
export async function requestSignOut(reason: "user" | "idle") {
  try {
    storage()?.setItem(SIGN_OUT_PENDING_KEY, "1");
  } catch {
    // storage blocked: the attempt below still runs
  }
  const headers = new Headers({ "Content-Type": "application/json" });
  addSessionHeaders(SIGN_OUT_URL, headers);
  try {
    const res = await fetch(SIGN_OUT_URL, {
      method: "POST",
      headers,
      body: JSON.stringify({ reason }),
      keepalive: true,
    });
    if (!res.ok) return null;
    try {
      storage()?.removeItem(SIGN_OUT_PENDING_KEY);
      storage()?.removeItem(LAST_ACTIVITY_KEY);
    } catch {
      // storage blocked
    }
    const body = await res.json().catch(() => null);
    return Boolean(body?.signed_out_everywhere);
  } catch {
    return null;
  }
}

/** A light request while someone is working without API calls (e.g. typing a long note):
 * the backend refreshes its tokens, which keeps single sign-on alive. False when the
 * backend no longer recognises the session. */
export async function heartbeat() {
  const headers = new Headers();
  addSessionHeaders(SESSION_URL, headers);
  try {
    const res = await fetch(SESSION_URL, { headers });
    return res.status !== 401 && res.status !== 403;
  } catch {
    return true; // network trouble is not a sign-out
  }
}

// Each tab marks its own messages, so it never acts on its own announcement.
const TAB_ID = Math.random().toString(36).slice(2);

/** Other tabs of this hospital learn about a sign-out at once, with its reason. */
export function announceSignOut(reason: EndReason) {
  try {
    const channel = new BroadcastChannel(CHANNEL);
    channel.postMessage({ type: "signed-out", reason, tab: TAB_ID });
    channel.close();
  } catch {
    // no BroadcastChannel: the other tabs' next request is refused by the backend
  }
}

export function onSignOutElsewhere(handler: (reason: EndReason) => void) {
  try {
    const channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = (event) => {
      const data = event.data as {
        type?: string;
        reason?: EndReason;
        tab?: string;
      };
      if (data?.type === "signed-out" && data.tab !== TAB_ID) {
        handler(data.reason ?? "signed_out");
      }
    };
    return () => channel.close();
  } catch {
    return () => undefined;
  }
}
