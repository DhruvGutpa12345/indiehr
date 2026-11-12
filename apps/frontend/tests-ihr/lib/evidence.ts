// IndieHR (P1-T1d): what a failed spec leaves behind, and nothing more. The repository and
// its Actions artifacts are public, so there is no trace, video or HTML report: only a
// screenshot per open page (skipped while a password or code field is on screen) and a small
// JSON file (final address, console errors, the assertion message), all with sign-in
// parameters, every test secret, typed code and session cookie value scrubbed.
import type { BrowserContext, Page, TestInfo } from "@playwright/test";
import { writeFileSync } from "node:fs";

import { secretValues } from "./users";

// Fields that may hold a password or a one-time code (Keycloak's pages and ours)
const SECRET_FIELDS =
  'input[type="password"], input[name="otp"], #otp, input[autocomplete="one-time-code"], input[name="totp"]';

const SESSION_COOKIE = /^(__Host-)?ihr_session$/;
// Sign-in parameters, anchored on the URL delimiter so that session_code is matched too
const SIGN_IN_PARAMS =
  /([?&#;])(code|session_code|state|session_state|nonce|code_challenge|code_verifier|execution|tab_id|client_data)=[^&#\s"')]*/gi;

const seenSecrets = new Set<string>();

/** Values seen in this process (typed one-time codes, session cookies): scrubbed from
 * evidence like the passwords. */
export function rememberSecret(value: string) {
  if (value) seenSecrets.add(value);
}

/** Any ihr_session value in a Set-Cookie header. */
function rememberSessionCookies(setCookie: string | null) {
  for (const line of (setCookie ?? "").split("\n")) {
    const [pair] = line.split(";");
    const at = pair.indexOf("=");
    if (at > 0 && SESSION_COOKIE.test(pair.slice(0, at).trim()))
      rememberSecret(pair.slice(at + 1).trim());
  }
}

export function scrub(text: string): string {
  let out = text.replace(SIGN_IN_PARAMS, "$1$2=[removed]");
  let values: string[] = [];
  try {
    values = secretValues();
  } catch {
    // no test users file: nothing of it can leak either
  }
  for (const value of [...values, ...seenSecrets]) {
    if (value) out = out.split(value).join("[masked]");
  }
  return out;
}

export class Evidence {
  readonly consoleErrors: string[] = [];
  private readonly contexts: BrowserContext[] = [];

  watch(context: BrowserContext) {
    this.contexts.push(context);
    context.on("console", (message) => {
      if (message.type() === "error") this.consoleErrors.push(message.text());
    });
    context.on("weberror", (error) =>
      this.consoleErrors.push(String(error.error())),
    );
    // Every session cookie the browser is given, so it can be scrubbed even after sign-out
    context.on("response", (response) => {
      response
        .headerValue("set-cookie")
        .then(rememberSessionCookies)
        .catch(() => undefined);
    });
  }

  private async rememberCurrentCookies() {
    for (const context of this.contexts) {
      try {
        for (const cookie of await context.cookies())
          if (SESSION_COOKIE.test(cookie.name)) rememberSecret(cookie.value);
      } catch {
        // context already closed
      }
    }
  }

  private pages(): Page[] {
    return this.contexts.flatMap((context) => context.pages());
  }

  async save(testInfo: TestInfo) {
    await this.rememberCurrentCookies();
    const shots: string[] = [];
    const skipped: string[] = [];
    let index = 0;
    for (const page of this.pages()) {
      index += 1;
      if (page.isClosed()) continue;
      const name = `page-${index}.png`;
      try {
        if ((await page.locator(SECRET_FIELDS).count()) > 0) {
          skipped.push(`${name}: a password or code field was on screen`);
          continue;
        }
        await page.screenshot({ path: testInfo.outputPath(name) });
        shots.push(name);
      } catch (error) {
        skipped.push(`${name}: ${String(error).split("\n")[0]}`);
      }
    }
    const summary = {
      test: testInfo.titlePath.join(" › "),
      status: testInfo.status,
      error: testInfo.errors.map((error) => error.message ?? String(error)),
      final_urls: this.pages()
        .filter((page) => !page.isClosed())
        .map((page) => page.url()),
      console_errors: this.consoleErrors.slice(-30),
      screenshots: shots,
      screenshots_skipped: skipped,
    };
    writeFileSync(
      testInfo.outputPath("failure.json"),
      scrub(JSON.stringify(summary, null, 2)),
    );
  }
}
