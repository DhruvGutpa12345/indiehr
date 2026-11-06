// IndieHR (P1-T4b, ADR-009): pure rules for the idle time-out, kept free of browser and app
// imports so they can be unit-tested with plain Node.

export type IdleState = "active" | "warn" | "expired";

export interface IdleSettings {
  idleSeconds: number; // sign out after this long without keyboard, mouse or touch input
  warnSeconds: number; // warn this long before
}

export const DEFAULT_IDLE: IdleSettings = {
  idleSeconds: 1800,
  warnSeconds: 120,
};

/** Seconds since the last input; an unknown or future time counts as just now. */
export function idleSecondsSince(lastActivityMs: number | null, nowMs: number) {
  if (lastActivityMs === null || !Number.isFinite(lastActivityMs)) return 0;
  return Math.max(0, Math.floor((nowMs - lastActivityMs) / 1000));
}

export function idleState(idle: number, settings: IdleSettings): IdleState {
  if (idle >= settings.idleSeconds) return "expired";
  if (idle >= settings.idleSeconds - settings.warnSeconds) return "warn";
  return "active";
}

/** Settings sent by the hospital's bootstrap script, with safe fallbacks. */
export function idleSettingsFrom(value: unknown): IdleSettings {
  const raw = (value ?? {}) as {
    idle_seconds?: unknown;
    warn_seconds?: unknown;
  };
  const idle = Number(raw.idle_seconds);
  const warn = Number(raw.warn_seconds);
  const idleSeconds = idle > 0 ? idle : DEFAULT_IDLE.idleSeconds;
  const warnSeconds =
    warn > 0 && warn < idleSeconds
      ? warn
      : Math.min(DEFAULT_IDLE.warnSeconds, idleSeconds / 2);
  return { idleSeconds, warnSeconds };
}

/** Only our own API gets the session headers (never a third-party URL). */
export function isOwnApi(url: string, origin: string) {
  if (url.startsWith("/") && !url.startsWith("//")) return true;
  try {
    return new URL(url).origin === origin;
  } catch {
    return false;
  }
}

/** A sign-in "next" path: same-origin paths only, never another site. */
export function safeNext(candidate: string | null, origin: string) {
  if (!candidate) return "/";
  try {
    const url = new URL(candidate, origin);
    if (url.origin !== origin) return "/";
    // A path starting "//" or "/\" would read as another site once used as a URL.
    const path = url.pathname.replace(/^[/\\]+/, "/");
    return path + url.search;
  } catch {
    return "/";
  }
}
