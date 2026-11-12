// IndieHR (P1-T1d): what the browser holds for a hospital, and what may stay after sign-out
// (Q-T9). Sentinels are planted under every key the clean-up owns; afterwards no sentinel may
// survive anywhere, and every key left must be on the reviewed allow-list (storage-keys.json)
// or have its content checked. A new key fails the spec until it is classified there.
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import type { BrowserContext, Page } from "@playwright/test";

export interface StorageSnapshot {
  local: Record<string, string>;
  session: Record<string, string>;
  caches: Record<string, string[]>; // cache name -> request URLs
}

export async function snapshot(page: Page): Promise<StorageSnapshot> {
  return page.evaluate(async () => {
    const dump = (store: Storage) => {
      const out: Record<string, string> = {};
      for (let i = 0; i < store.length; i++) {
        const key = store.key(i);
        if (key !== null) out[key] = store.getItem(key) ?? "";
      }
      return out;
    };
    const caches: Record<string, string[]> = {};
    if ("caches" in window) {
      for (const name of await window.caches.keys()) {
        const cache = await window.caches.open(name);
        caches[name] = (await cache.keys()).map((request) => request.url);
      }
    }
    return {
      local: dump(window.localStorage),
      session: dump(window.sessionStorage),
      caches,
    };
  });
}

const BOOT_COPY = "__ihrStorageAtBoot";

/**
 * Copy localStorage and sessionStorage at document start of every /login load in this
 * context, before any app code runs (the sign-in page's own start-up code removes some keys,
 * which would hide a broken clean-up). Read the copy with bootSnapshot().
 */
export async function captureStorageAtLoginBoot(context: BrowserContext) {
  await context.addInitScript((name) => {
    if (window.location.pathname !== "/login") return;
    const dump = (store: Storage) => {
      const out: Record<string, string> = {};
      for (let i = 0; i < store.length; i++) {
        const key = store.key(i);
        if (key !== null) out[key] = store.getItem(key) ?? "";
      }
      return out;
    };
    try {
      (window as unknown as Record<string, unknown>)[name] = {
        local: dump(window.localStorage),
        session: dump(window.sessionStorage),
        caches: {},
      };
    } catch {
      // storage blocked: the copy stays missing and bootSnapshot() fails
    }
  }, BOOT_COPY);
}

/** The storage copy taken before the app booted on this /login document. */
export async function bootSnapshot(page: Page): Promise<StorageSnapshot> {
  const copy = await page.evaluate(
    (name) =>
      (window as unknown as Record<string, unknown>)[name] as
        StorageSnapshot | undefined,
    BOOT_COPY,
  );
  if (!copy)
    throw new Error("no storage copy from the start of this /login load");
  return copy;
}

/** The persisted query cache's buster as the running app computes it (queryClient.ts). */
export async function appBuster(page: Page): Promise<string> {
  return page.evaluate(() => localStorage.getItem("app-version") ?? "0.0.0");
}

/**
 * A persisted query cache the app would restore, not discard: the app's own buster, a recent
 * timestamp and one dehydrated, successful query marked meta.persist (queryClient.ts only
 * saves those).
 */
export function persistedCacheSentinel(sentinel: string, buster: string) {
  const now = Date.now();
  const queryKey = ["ihr-sentinel", sentinel];
  return JSON.stringify({
    buster,
    timestamp: now,
    clientState: {
      mutations: [],
      queries: [
        {
          dehydratedAt: now,
          queryKey,
          queryHash: JSON.stringify(queryKey),
          meta: { persist: true },
          state: {
            data: { sentinel },
            dataUpdateCount: 1,
            dataUpdatedAt: now,
            error: null,
            errorUpdateCount: 0,
            errorUpdatedAt: 0,
            fetchFailureCount: 0,
            fetchFailureReason: null,
            fetchMeta: null,
            isInvalidated: false,
            status: "success",
            fetchStatus: "idle",
          },
        },
      ],
    },
  });
}

export function newSentinel() {
  return `ihr-sentinel-${randomBytes(6).toString("hex")}`;
}

/** The booking practitioner key ("user") with a planted value: cleared by clearStaffStorage
 * since P1-T1d, so only bundles built with that patch pass a check that plants it. */
export function bookingUserSentinel(sentinel: string) {
  return {
    local: {
      user: JSON.stringify({ username: sentinel, first_name: sentinel }),
    } as Record<string, string>,
  };
}

/**
 * Keys the staff clean-up owns (clearStaffStorage), with a planted value each. Pass the app's
 * buster (appBuster) so the persisted query cache is one the app would restore.
 */
export function staffSentinels(sentinel: string, buster = "0.0.0") {
  const json = (extra: Record<string, unknown> = {}) =>
    JSON.stringify({ sentinel, ...extra });
  return {
    local: {
      care_access_token: sentinel,
      care_refresh_token: sentinel,
      mfa_temp_token: sentinel,
      REACT_QUERY_OFFLINE_CACHE: persistedCacheSentinel(sentinel, buster),
      "pharmacy-dispense-service--sentinel-facility": json(),
      users: sentinel,
      resource: sentinel,
      appointments: sentinel,
      "filters--/facility/sentinel/patients": json({ name: sentinel }),
      "payment_reconcilation_location_cache--sentinel-facility": json(),
      "care_queue_practitioner--sentinel-facility": json(),
      care_patient_token: json({ token: sentinel, createdAt: "2000-01-01" }),
    } as Record<string, string>,
    session: {
      "care-auth-user": json({ username: sentinel }),
      encounter_list_filters: json(),
      encounter_history_filters: json(),
      [`${sentinel}-key`]: sentinel,
    } as Record<string, string>,
  };
}

export async function plant(
  page: Page,
  values: { local?: Record<string, string>; session?: Record<string, string> },
) {
  await page.evaluate((v) => {
    for (const [key, value] of Object.entries(v.local ?? {}))
      localStorage.setItem(key, value);
    for (const [key, value] of Object.entries(v.session ?? {}))
      sessionStorage.setItem(key, value);
  }, values);
}

// Reviewed: keys that may remain after sign-out (../storage-keys.json, shared with
// scripts/check-storage-keys.py, which checks every key in src/ is classified). None holds a
// person, a patient or a token.
interface Classified {
  may_remain: {
    exact: Record<string, string>;
    prefixes: Record<string, string>;
  };
  content_checked: { local: string[]; session: string[] };
}
const CLASSIFIED = JSON.parse(
  readFileSync(join(__dirname, "..", "storage-keys.json"), "utf8"),
) as Classified;
const ALLOWED_EXACT = new Set(Object.keys(CLASSIFIED.may_remain.exact));
const ALLOWED_PREFIXES = Object.keys(CLASSIFIED.may_remain.prefixes);

// Kept, but only with this content (the sign-in page rewrites them empty)
const CONTENT_CHECKED: Record<string, (value: string) => string | null> = {
  REACT_QUERY_OFFLINE_CACHE: (value) => {
    const parsed = safeJson(value) as {
      clientState?: { queries?: unknown[]; mutations?: unknown[] };
    } | null;
    const queries = parsed?.clientState?.queries ?? [];
    const mutations = parsed?.clientState?.mutations ?? [];
    return queries.length || mutations.length
      ? `persisted query cache still holds ${queries.length} queries and ${mutations.length} mutations`
      : null;
  },
};
const SESSION_CONTENT_CHECKED: Record<
  string,
  (value: string) => string | null
> = {
  "care-auth-user": (value) => {
    const parsed = safeJson(value);
    const empty =
      parsed === null ||
      parsed === undefined ||
      parsed === "" ||
      (typeof parsed === "object" && Object.keys(parsed).length === 0);
    return empty || value === "undefined"
      ? null
      : "care-auth-user still holds a user";
  },
};

// The content checks above must be exactly the keys storage-keys.json names
for (const [kind, checks] of [
  ["local", CONTENT_CHECKED],
  ["session", SESSION_CONTENT_CHECKED],
] as const) {
  const named = [...CLASSIFIED.content_checked[kind]].sort().join(",");
  if (Object.keys(checks).sort().join(",") !== named)
    throw new Error(
      `storage-keys.json content_checked.${kind} differs from lib/storage.ts`,
    );
}

function safeJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

/** Planted values that are no longer in storage (empty list = all still there). */
export async function missingPlants(
  page: Page,
  values: { local?: Record<string, string>; session?: Record<string, string> },
): Promise<string[]> {
  const state = await snapshot(page);
  const missing: string[] = [];
  for (const [key, value] of Object.entries(values.local ?? {}))
    if (state.local[key] !== value) missing.push(`localStorage "${key}"`);
  for (const [key, value] of Object.entries(values.session ?? {}))
    if (state.session[key] !== value) missing.push(`sessionStorage "${key}"`);
  return missing;
}

/**
 * Problems with what is left after sign-out (empty list = clean): the page's storage now, or
 * a given copy of it (bootSnapshot), plus the cookies of the page's origin.
 */
export async function leftovers(
  page: Page,
  context: BrowserContext,
  sentinel: string,
  sessionCookieName: string,
  copy?: StorageSnapshot,
): Promise<string[]> {
  const problems: string[] = [];
  const state = copy ?? (await snapshot(page));
  for (const [key, value] of Object.entries(state.local)) {
    if (key.includes(sentinel) || value.includes(sentinel))
      problems.push(`localStorage "${key}" still holds a planted value`);
    const check = CONTENT_CHECKED[key];
    if (check) {
      const problem = check(value);
      if (problem) problems.push(problem);
    } else if (
      !ALLOWED_EXACT.has(key) &&
      !ALLOWED_PREFIXES.some((prefix) => key.startsWith(prefix))
    ) {
      problems.push(`localStorage "${key}" is not on the reviewed allow-list`);
    }
  }
  for (const [key, value] of Object.entries(state.session)) {
    if (key.includes(sentinel) || value.includes(sentinel))
      problems.push(`sessionStorage "${key}" still holds a planted value`);
    const check = SESSION_CONTENT_CHECKED[key];
    if (check) {
      const problem = check(value);
      if (problem) problems.push(problem);
    } else {
      problems.push(
        `sessionStorage "${key}" is not on the reviewed allow-list`,
      );
    }
  }
  for (const [name, urls] of Object.entries(state.caches)) {
    for (const url of urls) {
      if (url.includes(sentinel) || new URL(url).pathname.startsWith("/api/"))
        problems.push(`Cache Storage "${name}" holds ${new URL(url).pathname}`);
    }
  }
  const origin = new URL(page.url()).origin;
  for (const cookie of await context.cookies(origin)) {
    if (cookie.name === sessionCookieName)
      problems.push(`cookie ${cookie.name} is still set`);
    if (cookie.value.includes(sentinel))
      problems.push(`cookie ${cookie.name} holds a planted value`);
  }
  return problems;
}

/** The service worker's caches: only Workbox's precache of static files, never an API reply. */
export function apiCacheProblems(caches: Record<string, string[]>): string[] {
  const problems: string[] = [];
  for (const [name, urls] of Object.entries(caches)) {
    if (!name.startsWith("workbox-precache"))
      problems.push(`unexpected cache "${name}"`);
    for (const url of urls) {
      if (new URL(url).pathname.startsWith("/api/"))
        problems.push(`cache "${name}" holds ${new URL(url).pathname}`);
    }
  }
  return problems;
}
