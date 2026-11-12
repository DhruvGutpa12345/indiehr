// IndieHR (P1-T1d): one-time codes for the browser specs, matching Keycloak's TOTP and our
// Python rule (plugins/ihr_platform/ihr_platform/keycloak/totp.py): HMAC-SHA1 over the UTF-8
// bytes of the stored secret, 6 digits, 30-second steps.
//
// Keycloak refuses a code twice (otpPolicyCodeReusable false) and locks an account after five
// wrong codes, so codes are handed out from a per-user counter that only moves forward and is
// kept in a file (a failed test restarts the worker process). A refused code is never retried:
// once Keycloak refuses one, every later sign-in of that run stops before typing anything. The
// next run starts by clearing only that flag (clearRefused, from global-setup.ts); the used
// steps stay, so a code is never typed twice across back-to-back runs either.
import { createHmac } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const PERIOD_SECONDS = 30;
// Keycloak accepts the step before and after the current one (otpPolicyLookAheadWindow 1).
const LOOK_AHEAD = 1;

/** The 6-digit code for one 30-second step (RFC 6238, HMAC-SHA1). */
export function codeForStep(rawSecret: string, step: number): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(step));
  const digest = createHmac("sha1", Buffer.from(rawSecret, "utf8"))
    .update(counter)
    .digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const value = (digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return String(value).padStart(6, "0");
}

export function stepAt(epochMs: number): number {
  return Math.floor(epochMs / 1000 / PERIOD_SECONDS);
}

interface State {
  used: Record<string, number>; // username -> last step handed out (never a code)
  refused?: string; // set once Keycloak refused a code: no further codes are typed
}

export function statePath(): string {
  return process.env.IHR_E2E_STATE ?? join(tmpdir(), "ihr-e2e-totp-state.json");
}

function readState(): State {
  const path = statePath();
  if (!existsSync(path)) return { used: {} };
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8")) as State;
    return { used: parsed.used ?? {}, refused: parsed.refused };
  } catch {
    return { used: {} };
  }
}

function writeState(state: State) {
  writeFileSync(statePath(), JSON.stringify(state), { mode: 0o600 });
}

/** Stop all later sign-ins: Keycloak refused a code for this user. */
export function markRefused(username: string) {
  const state = readState();
  state.refused = username;
  writeState(state);
}

export function refusedEarlier(): string | undefined {
  return readState().refused;
}

/** Start of a run: forget an earlier run's refusal, keep the used steps. */
export function clearRefused() {
  if (!existsSync(statePath())) return;
  const state = readState();
  if (state.refused === undefined) return;
  delete state.refused;
  writeState(state);
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * A code this user has never been given: the current step, or a later one when the current
 * step was already used. Waits (at most one step) when even the look-ahead step was used.
 * The step is recorded as used before the code is returned, so it is never typed twice.
 */
export async function freshCode(
  username: string,
  rawSecret: string,
  now: () => number = Date.now,
): Promise<string> {
  const refused = refusedEarlier();
  if (refused) {
    throw new Error(
      `Keycloak refused an earlier code in this run (for ${refused}), so no more codes are typed and no account is locked. State file: ${statePath()} (the next run clears the flag)`,
    );
  }
  const state = readState();
  const last = state.used[username] ?? -1;
  let current = stepAt(now());
  let step = Math.max(current, last + 1);
  if (step > current + LOOK_AHEAD) {
    // Wait until the step before it is current, so "step" is inside the look-ahead window.
    const waitMs = (step - LOOK_AHEAD) * PERIOD_SECONDS * 1000 - now() + 250;
    if (waitMs > 0) await sleep(waitMs);
    current = stepAt(now());
    step = Math.max(current, last + 1);
  }
  // Too close to the end of the window for the current step: the next one is accepted too.
  const intoStep = (now() / 1000) % PERIOD_SECONDS;
  if (step === current && intoStep > PERIOD_SECONDS - 2) step = current + 1;
  state.used[username] = step;
  writeState(state);
  return codeForStep(rawSecret, step);
}
