// IndieHR P1-T31 (docs/reviews/p1-t31/design.md §4, attack.md D4, D10, D12, R7): the hospital
// switcher's rules. Pure (no React, no "@/" imports), so `node --import tsx --test`
// runs hospitals.test.ts.
//
// The backend (GET /api/ihr/my-hospitals/) builds each address from its own Domain rows. This
// file is the browser's second check, so a wrong answer can never send staff somewhere that is
// not a hospital's sign-in start. An entry is kept only when its address
//   - is http or https and has the page's own scheme,
//   - has a host and no credentials,
//   - has the path /api/ihr/auth/login/ exactly, and no query and no fragment.
// A hospital's own address can be any host (a custom domain), so no sibling-host rule.

export interface HospitalEntry {
  name: string;
  url: string;
  current: boolean;
  available: boolean;
}

export interface HospitalsAnswer {
  hospitals: HospitalEntry[];
}

/** The one path a switch goes to: the target hospital's sign-in start (SSO signs the person in). */
export const SWITCH_PATH = "/api/ihr/auth/login/";
/** TanStack's staleTime for the list (nothing is persisted, nothing is invalidated). */
export const HOSPITALS_STALE_MS = 5 * 60 * 1000;
/** The list is the signed-in person's own: the key carries the username (D12). */
export const hospitalsQueryKey = (username: string) =>
  ["ihr", "my-hospitals", username] as const;

export type EntryKind = "current" | "unavailable" | "other";

export function entryKind(entry: HospitalEntry): EntryKind {
  if (entry.current) return "current";
  return entry.available ? "other" : "unavailable";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The entry as a clean object (flags read strictly), or null when it fails a rule. */
function checked(raw: unknown, pageProtocol: string): HospitalEntry | null {
  if (!isRecord(raw)) return null;
  const { name, url } = raw;
  if (typeof name !== "string" || name.trim() === "") return null;
  if (typeof url !== "string" || /[?#\\\s]/.test(url)) return null;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  if (parsed.protocol !== pageProtocol) return null;
  if (parsed.hostname === "") return null;
  if (parsed.username !== "" || parsed.password !== "") return null;
  if (parsed.pathname !== SWITCH_PATH) return null;
  if (parsed.search !== "" || parsed.hash !== "") return null;
  return {
    name,
    url,
    current: raw.current === true,
    available: raw.available === true,
  };
}

/**
 * The entries to show: only the ones that pass the rules, the current hospital first and then by
 * name (case ignored). `null` when fewer than two remain: the group is then not shown (Q-K146).
 */
export function switcherEntries(
  answer: unknown,
  pageProtocol: string,
): HospitalEntry[] | null {
  if (!isRecord(answer) || !Array.isArray(answer.hospitals)) return null;
  const seen = new Set<string>();
  const kept: HospitalEntry[] = [];
  for (const raw of answer.hospitals) {
    const entry = checked(raw, pageProtocol);
    if (entry === null || seen.has(entry.url)) continue;
    seen.add(entry.url);
    kept.push(entry);
  }
  if (kept.length < 2) return null;
  return kept.sort((a, b) => {
    if (a.current !== b.current) return a.current ? -1 : 1;
    const x = a.name.toLowerCase();
    const y = b.name.toLowerCase();
    return x < y ? -1 : x > y ? 1 : 0;
  });
}

/**
 * What the passive menu renders from a query's state: nothing while loading and nothing on any
 * error (it never toasts or redirects, R7), otherwise `switcherEntries`.
 */
export function menuEntries(
  state: { data: unknown; isError: boolean },
  pageProtocol: string,
): HospitalEntry[] | null {
  if (state.isError || state.data === undefined) return null;
  return switcherEntries(state.data, pageProtocol);
}
