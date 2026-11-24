// IndieHR P1-T11c3 (design-c.md §13.3, SS1): the ONE place that knows whether MRD's records
// grants still stand. Mounted once around every staff page (IhrStaffPage.tsx, P2-T0c); it acts
// on the facility routes for users who may ask for records access, and on CARE's facility-less
// patient routes for restricted users (review F6); elsewhere it is inert (scope null). The banner on the patient page and the records page's list read
// its state (useIhrGrants) and run no timer of their own.
//
// It asks the backend (GET /api/ihr/records-access/, ids and times only: the poll shows no
// patient and writes no audit entry, review BA1), one look at a time and none dropped
// (records.ts serialLooks), each limited to 10 s (timedLook):
//   - on mount, every 30 s, when the window gets focus or becomes visible;
//   - at the moment the earliest grant runs out (one timer);
//   - when a chart opens in CARE's view (useIhrOpenChart);
//   - at once when ANY request of the app is refused (403) while a grant is held or a chart is
//     open: a grant an administrator ended shows up as refusals on the open record, so this is
//     how it is found in seconds, not at the next 30-second look;
//   - when another tab says its grants changed (BroadcastChannel "ihr-grants"; where that is
//     missing, a `storage` event of the key ihr_grants_changed).
// A new grant is held from its POST answer at once (noteGrant, review F2). A grant that is gone
// or has run out, a chart no live grant covers, or a list that could not be had twice in a row
// while a grant was held or a chart open (records.ts MAX_FAILURES_WHILE_HELD) clears every
// cached answer (CARE's query keys for clinical data vary by page; clearing is the only way to
// be sure none stays on screen), says so in a toast, and replaces the page by the records page
// (or the home page outside a facility), so Back does not return to the record. The rules
// (what counts as ended) are records.ts watchStep, tested without React.
import type { QueryKey } from "@tanstack/react-query";
import { t } from "i18next";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useSyncExternalStore,
} from "react";
import { toast } from "sonner";

import useAuthUser from "@/hooks/useAuthUser";

import { ihrRecordsApi } from "@/Utils/ihr/ihrApi";
import {
  type GrantSummary,
  LOOK_TIMEOUT_MS,
  WATCH_START,
  type WatchAction,
  type WatchEvent,
  type WatchState,
  earliestExpiryMs,
  isRefusal,
  secondsLeftAt,
  serialLooks,
  timedLook,
  watchStep,
} from "@/Utils/ihr/records";
import { useIhrAccess, useRestrictedAnywhere } from "@/Utils/ihr/useIhrAccess";
import { callApi } from "@/Utils/request/query";
import queryClient from "@/Utils/request/queryClient";

const CHANNEL = "ihr-grants";
const STORAGE_KEY = "ihr_grants_changed";
const POLL_MS = 30_000;
const TICK_MS = 1_000;
const REFUSAL_DELAY_MS = 500;
const EXPIRY_SLACK_MS = 250;
const TAB_ID = Math.random().toString(36).slice(2);

export interface GrantsView {
  /** "off": no watcher is mounted (not MRD, or outside a facility); "loading": no answer yet */
  status: "off" | "loading" | "ready";
  grants: readonly GrantSummary[];
  /** the length of a new grant in minutes, from the backend */
  grantMinutes: number | undefined;
  /** the clock reading when `grants` was fetched (their seconds_left count from it) */
  fetchedAtMs: number;
  /** the clock reading of the last one-second tick (re-renders the countdown) */
  nowMs: number;
}

const OFF: GrantsView = {
  status: "off",
  grants: [],
  grantMinutes: undefined,
  fetchedAtMs: 0,
  nowMs: 0,
};

// One shared state for the whole page (a module store: the watcher writes, hooks read).
let view: GrantsView = OFF;
let tracker: WatchState = WATCH_START;
const listeners = new Set<() => void>();
/** patients whose chart is open in CARE's view for a restricted records user (count per page) */
const openCharts = new Map<string, number>();

function chartsOpen(): string[] {
  return [...openCharts.keys()];
}

function setView(next: GrantsView) {
  view = next;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}

/** The live grants, their count-down and the actions on them. */
export function useIhrGrants() {
  const state = useSyncExternalStore(subscribe, () => view);
  const secondsLeft = useCallback(
    (grant: GrantSummary) =>
      secondsLeftAt(grant, state.fetchedAtMs, state.nowMs),
    [state.fetchedAtMs, state.nowMs],
  );
  return { ...state, secondsLeft };
}

// --- other tabs ---------------------------------------------------------------------------

/** Tell the other tabs that this tab created or ended a grant. */
export function announceGrantsChanged() {
  try {
    const channel = new BroadcastChannel(CHANNEL);
    channel.postMessage({ type: "changed", tab: TAB_ID });
    channel.close();
  } catch {
    // no BroadcastChannel: the storage event below
  }
  try {
    localStorage.setItem(STORAGE_KEY, `${TAB_ID}:${Date.now()}`);
  } catch {
    // storage blocked: the other tabs' next look finds out
  }
}

// --- the watcher's own actions ------------------------------------------------------------

let controller: {
  refetch: () => Promise<void>;
  event: (event: WatchEvent) => void;
} | null = null;

/**
 * Ask the backend now (after creating or ending a grant). The promise settles on an answer
 * whose GET was sent after this call (a look already running is followed by one more).
 */
export function refreshGrants(): Promise<void> {
  return controller ? controller.refetch() : Promise.resolve();
}

/**
 * Hold a grant this tab just made, from the POST 201 body (review F2): the banner shows at once,
 * and an end is found even if no list ever names it (the first look fails, or one sent before
 * the POST answers without it).
 */
export function noteGrant(grant: GrantSummary) {
  controller?.event({ type: "noted", grant, atMs: Date.now() });
}

/**
 * A page that shows a patient's chart in CARE's own view to a restricted records user (the
 * patient gates, IhrGates.tsx IhrRouteGate): for them that view exists only under a grant, so the watcher
 * treats it as held (records.ts watchStep openCharts) and takes a look when it opens.
 */
export function useIhrOpenChart(patientId: string | undefined) {
  useEffect(() => {
    if (!patientId) return;
    openCharts.set(patientId, (openCharts.get(patientId) ?? 0) + 1);
    void controller?.refetch();
    return () => {
      const left = (openCharts.get(patientId) ?? 1) - 1;
      if (left > 0) openCharts.set(patientId, left);
      else openCharts.delete(patientId);
    };
  }, [patientId]);
}

/**
 * End one grant of this user (End now): DELETE, then look again. The look finds the grant gone
 * and the watcher ends the page (cache cleared, toast, records page): one path for every end.
 */
export async function endGrantNow(id: number): Promise<boolean> {
  try {
    await callApi(ihrRecordsApi.end, {
      pathParams: { id: String(id) },
      silent: true,
    });
  } catch (error) {
    const status = (error as { status?: number }).status;
    if (status !== 404) {
      // 404: already ended elsewhere, the look below shows it
      toast.error(t("ihr_records_end_failed"));
      return false;
    }
  }
  announceGrantsChanged();
  await refreshGrants();
  return true;
}

function isOwnQuery(key: QueryKey): boolean {
  return Array.isArray(key) && key[0] === "ihr";
}

interface WatcherProps {
  children?: ReactNode;
  /**
   * routeGates.ts watcherScope of the page's route id. "facility": a facility page, the
   * watcher runs for users who may ask for records access there. "facilityLess": CARE's patient
   * routes with no facility (/patient/..., /organization/.../patient/..., review F6): the
   * records slug is a facility's, so there it runs for any restricted user (GET
   * /api/ihr/my-access/ without a facility); its poll shows no patient, and a non-MRD's list is
   * empty. null: inert.
   */
  scope: "facility" | "facilityLess" | null;
  /** the facility of the page's route match (IhrStaffPage), never the address bar's */
  facilityId: string | undefined;
}

export function IhrGrantWatcher({ children, scope, facilityId }: WatcherProps) {
  const access = useIhrAccess({ facilityId });
  const user = useAuthUser();
  const outside = scope === "facilityLess" && !facilityId;
  const restrictedAnywhere = useRestrictedAnywhere(
    outside && !user.is_superuser,
  );
  const enabled =
    scope === null
      ? false
      : outside
        ? restrictedAnywhere === true
        : !!facilityId && access.mode !== "pending" && access.records;
  const facilityRef = useRef(facilityId);
  useEffect(() => {
    facilityRef.current = facilityId;
  }, [facilityId]);

  useEffect(() => {
    if (!enabled) return;

    let alive = true;
    // aborts a look still running when the watcher unmounts (review F4)
    const stopLooks = new AbortController();
    let expiryTimer: ReturnType<typeof setTimeout> | undefined;
    let refusalTimer: ReturnType<typeof setTimeout> | undefined;

    const act = (action: WatchAction) => {
      if (action === "none") return;
      // every cached answer goes: clinical keys vary by page (R7: the non-clinical ones refetch)
      queryClient.clear();
      toast.info(
        t(action === "ended" ? "ihr_records_ended" : "ihr_records_unconfirmed"),
      );
      const facility = facilityRef.current;
      // replace: Back must not return to the record the grant opened; outside a facility
      // (CARE's facility-less patient routes, review F6) the home page. A full page load
      // (P2-T0c): after clear() the router does not remount a page it already shows, so the
      // cleared session and access queries would never refetch (CI: stuck on Loading); a
      // reload also leaves nothing of the grant in this tab's memory
      window.location.replace(
        facility ? `/facility/${facility}/ihr/records` : "/",
      );
    };

    const armExpiry = () => {
      clearTimeout(expiryTimer);
      const at = earliestExpiryMs(view.grants, view.fetchedAtMs);
      if (at === null) return;
      expiryTimer = setTimeout(
        () => {
          step({ type: "tick", atMs: Date.now() });
          void refetch();
        },
        Math.max(0, at - Date.now()) + EXPIRY_SLACK_MS,
      );
    };

    const step = (event: WatchEvent) => {
      const before = tracker;
      const result = watchStep(before, event);
      tracker = result.state;
      if (event.type === "listed") {
        // grants noted after this GET was sent stay (watchStep keeps them in `held`)
        const listedIds = new Set(event.grants.map((g) => g.id));
        const kept = result.state.held
          .filter((h) => !listedIds.has(h.id))
          .map((h) => ({
            id: h.id,
            patient: h.patient,
            expires_at: new Date(h.expiresAtMs).toISOString(),
            seconds_left: Math.max(
              0,
              Math.floor((h.expiresAtMs - event.atMs) / 1000),
            ),
          }));
        setView({
          status: "ready",
          grants: [...event.grants, ...kept],
          grantMinutes: view.grantMinutes,
          fetchedAtMs: event.atMs,
          nowMs: event.atMs,
        });
        armExpiry();
      } else if (event.type === "noted" && result.state !== before) {
        // seconds_left counted from this view's fetchedAtMs, like the listed ones
        const at = event.atMs;
        const base = view.grants.length > 0 ? view.fetchedAtMs : at;
        const grant = {
          ...event.grant,
          seconds_left:
            event.grant.seconds_left + Math.floor((at - base) / 1000),
        };
        setView({
          ...view,
          status: "ready",
          grants: [...view.grants.filter((g) => g.id !== grant.id), grant],
          fetchedAtMs: base,
          nowMs: at,
        });
        armExpiry();
      } else if (
        (event.type === "failed" || event.type === "refused") &&
        result.action !== "none"
      ) {
        setView({ ...view, grants: [] });
      } else if (event.type === "tick" && result.action !== "none") {
        const live = new Set(result.state.held.map((g) => g.id));
        setView({
          ...view,
          grants: view.grants.filter((g) => live.has(g.id)),
          nowMs: event.atMs,
        });
      }
      act(result.action);
    };

    // one look: a GET with a 10 s limit; a hang, an abort or an error is a failure
    async function look() {
      if (!alive) return;
      const sentAtMs = Date.now();
      try {
        const list = await timedLook(
          (signal) => callApi(ihrRecordsApi.list, { silent: true, signal }),
          LOOK_TIMEOUT_MS,
          stopLooks.signal,
        );
        if (!alive) return;
        const now = Date.now();
        // grantMinutes first: step() copies it into the view it writes
        view = { ...view, grantMinutes: list.grant_minutes };
        step({
          type: "listed",
          grants: list.results,
          atMs: now,
          sentAtMs,
          openCharts: chartsOpen(),
        });
      } catch {
        if (alive) step({ type: "failed", openCharts: chartsOpen() });
      }
    }
    // a look asked for during another one is sent after it, never dropped (F3, TS4)
    const refetch = serialLooks(look);

    controller = {
      refetch,
      event: step,
    };
    // `tracker` is kept from an earlier mount: a grant that ended while the watcher was away is
    // still found out by the first look
    if (view.status === "off") {
      setView({ ...OFF, status: "loading", nowMs: Date.now() });
    }
    void refetch();

    const poll = setInterval(() => void refetch(), POLL_MS);
    // the one-second tick re-renders the countdown, and ends a grant at its time even when the
    // browser delays the expiry timer (a background tab)
    const ticker = setInterval(() => {
      const nowMs = Date.now();
      if (tracker.held.length > 0) step({ type: "tick", atMs: nowMs });
      if (view.grants.length > 0) setView({ ...view, nowMs });
    }, TICK_MS);

    const onVisible = () => {
      if (document.visibilityState === "visible") void refetch();
    };
    const onFocus = () => void refetch();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);

    // a refusal anywhere, while a grant is held or a chart open: look at once (the end is found
    // in seconds). Before any list, a refusal on an open chart counts like a failed look (F2).
    const onRefusal = (status: unknown) => {
      if (!isRefusal(status)) return;
      const charts = chartsOpen();
      step({ type: "refused", openCharts: charts });
      if ((tracker.held.length === 0 && charts.length === 0) || refusalTimer)
        return;
      refusalTimer = setTimeout(() => {
        refusalTimer = undefined;
        void refetch();
      }, REFUSAL_DELAY_MS);
    };
    const unsubscribeQueries = queryClient
      .getQueryCache()
      .subscribe((event) => {
        if (
          event.type === "updated" &&
          event.action.type === "error" &&
          !isOwnQuery(event.query.queryKey)
        ) {
          onRefusal(
            (event.action.error as { status?: unknown } | null)?.status,
          );
        }
      });
    const unsubscribeMutations = queryClient
      .getMutationCache()
      .subscribe((event) => {
        if (event.type === "updated" && event.action.type === "error") {
          onRefusal(
            (event.action.error as { status?: unknown } | null)?.status,
          );
        }
      });

    // other tabs
    let channel: BroadcastChannel | undefined;
    try {
      channel = new BroadcastChannel(CHANNEL);
      channel.onmessage = (message) => {
        const data = message.data as { type?: string; tab?: string };
        if (data?.type === "changed" && data.tab !== TAB_ID) void refetch();
      };
    } catch {
      channel = undefined;
    }
    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) void refetch();
    };
    window.addEventListener("storage", onStorage);

    return () => {
      alive = false;
      stopLooks.abort();
      controller = null;
      clearInterval(poll);
      clearInterval(ticker);
      clearTimeout(expiryTimer);
      clearTimeout(refusalTimer);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("storage", onStorage);
      unsubscribeQueries();
      unsubscribeMutations();
      channel?.close();
      // `tracker` stays: a remount (route change inside the facility) must still know what was held
      if (tracker.held.length === 0) setView(OFF);
    };
  }, [enabled]);

  return <>{children}</>;
}
