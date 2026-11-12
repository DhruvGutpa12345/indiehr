// IndieHR (P1-T1d): shared pieces of the browser specs. Each spec opens its own browser
// contexts (separate cookie jars and Keycloak single sign-on), signs in through the real
// Keycloak pages, and leaves only scrubbed evidence when it fails (./evidence.ts).
import {
  type Browser,
  type BrowserContext,
  type BrowserContextOptions,
  type Page,
  test as base,
  expect,
} from "@playwright/test";

import { Evidence, rememberSecret } from "./evidence";
import { freshCode, markRefused, statePath } from "./totp";
import { type TestUser, testUser } from "./users";

export { expect };

export const HOSPITALS = {
  alpha: "http://alpha.localhost:8080",
  beta: "http://beta.localhost:8080",
} as const;
export type Hospital = keyof typeof HOSPITALS;
export const KEYCLOAK = "http://auth.localhost:8081";
export const COOKIE = "ihr_session"; // dev name (production: __Host-ihr_session)
const SIGN_IN_PATH = "/api/ihr/auth/login/"; // staffSession.ts SIGN_IN_URL

// browser.newContext() does not take the config's `use` options, so they are repeated here.
export const CONTEXT_OPTIONS: BrowserContextOptions = {
  locale: "en",
  timezoneId: "Asia/Kolkata",
  viewport: { width: 1366, height: 900 },
  serviceWorkers: "allow",
};

export const SIGN_IN_BUTTON = "Sign in with your hospital account";
export const NOT_MEMBER = "You are not a member of this hospital.";

interface Fixtures {
  /** A fresh browser context (own cookies, storage and Keycloak session), watched for evidence. */
  newContext: () => Promise<BrowserContext>;
}

export const test = base.extend<Fixtures>({
  newContext: async ({ browser }: { browser: Browser }, use, testInfo) => {
    const evidence = new Evidence();
    const contexts: BrowserContext[] = [];
    await use(async () => {
      const context = await browser.newContext(CONTEXT_OPTIONS);
      evidence.watch(context);
      contexts.push(context);
      return context;
    });
    if (testInfo.status !== testInfo.expectedStatus) {
      await evidence.save(testInfo);
    }
    for (const context of contexts) await context.close();
  },
});

/** Our API as the web app calls it (same origin, our CSRF header), from inside the page. */
export async function apiStatus(page: Page, path: string): Promise<number> {
  return page.evaluate(async (url) => {
    const res = await fetch(url, {
      headers: { "X-IHR-CSRF": "1" },
      credentials: "same-origin",
    });
    return res.status;
  }, path);
}

/**
 * Whether this context holds the hospital's session cookie. Only presence matters, so a failed
 * assertion prints true or false, never the cookie's value.
 */
export async function hasSessionCookie(
  context: BrowserContext,
  hospital: Hospital,
): Promise<boolean> {
  const cookies = await context.cookies(HOSPITALS[hospital]);
  return cookies.some((cookie) => cookie.name === COOKIE);
}

/**
 * The session cookie itself, for the few checks that compare or copy its value. Never pass it
 * (or its value) to expect(): compare inside the spec and assert on the boolean. Every value
 * read here is also scrubbed from failure evidence.
 */
export async function sessionCookieForComparison(
  context: BrowserContext,
  hospital: Hospital,
) {
  const cookies = await context.cookies(HOSPITALS[hospital]);
  const cookie = cookies.find((item) => item.name === COOKIE);
  if (cookie) rememberSecret(cookie.value);
  return cookie;
}

/**
 * Sign in at a hospital through Keycloak: password, then a fresh one-time code. Returns once
 * the hospital's session API answers for this user. A refused code stops the whole run's
 * sign-ins (never retried, so no account is locked).
 */
export async function signIn(page: Page, hospital: Hospital, user: TestUser) {
  if (!user.rawSecret) throw new Error(`${user.username} has no pre-set code`);
  const origin = HOSPITALS[hospital];
  await page.goto(`${origin}/login`);
  await page.getByRole("button", { name: SIGN_IN_BUTTON }).click();
  await page.waitForURL((url) => url.origin === KEYCLOAK);
  await page.locator("#username").fill(user.username);
  await page.locator("#password").fill(user.password);
  await page.locator("#kc-login").click();
  const otp = page.locator("#otp");
  await otp.waitFor({ timeout: 20_000 });
  const code = await freshCode(user.username, user.rawSecret);
  rememberSecret(code);
  await otp.fill(code);
  await page.locator("#kc-login").click();
  try {
    await page.waitForURL(
      (url) => url.origin === origin && !url.pathname.startsWith("/api/"),
      { timeout: 30_000 },
    );
  } catch (error) {
    if (new URL(page.url()).origin === KEYCLOAK && (await otp.count()) > 0) {
      markRefused(user.username);
      throw new Error(
        `Keycloak refused the code for ${user.username}; later sign-ins in this run stop. State file: ${statePath()}`,
      );
    }
    throw error;
  }
  await expectSignedIn(page);
}

/** Single sign-on at a second hospital: no password or code is asked for. */
export async function signInBySso(page: Page, hospital: Hospital) {
  const origin = HOSPITALS[hospital];
  await page.goto(`${origin}/login`);
  // The page is on <origin>/login now, which a plain "back at the hospital" wait would match
  // at once. The round trip (our sign-in route, Keycloak, the callback) is one chain of
  // redirects that commits only at its end, so first wait for its first request, then for the
  // final address: anything but /login, or /login?error=… (which fails here, not on time-out).
  const left = page.waitForRequest(
    (request) =>
      request.isNavigationRequest() &&
      new URL(request.url()).pathname.startsWith(SIGN_IN_PATH),
    { timeout: 30_000 },
  );
  await page.getByRole("button", { name: SIGN_IN_BUTTON }).click();
  await left;
  await page.waitForURL(
    (url) =>
      url.origin === origin &&
      !url.pathname.startsWith("/api/") &&
      (url.pathname !== "/login" || url.searchParams.has("error")),
    { timeout: 30_000 },
  );
  const error = new URL(page.url()).searchParams.get("error");
  if (error)
    throw new Error(`single sign-on at ${hospital} ended with error=${error}`);
  await expectSignedIn(page);
}

export async function expectSignedIn(page: Page) {
  // A navigation still in progress destroys the page's context mid-fetch: count that as
  // "not yet" (-1) instead of failing the poll.
  await expect
    .poll(
      async () => {
        try {
          return await apiStatus(page, "/api/ihr/auth/session/");
        } catch {
          return -1;
        }
      },
      { timeout: 20_000 },
    )
    .toBe(200);
}

/** The signed-in home page (UserDashboard) has rendered for this user. */
export async function expectHome(page: Page) {
  await expect(page.getByText(/^Hey /)).toBeVisible();
}

/** "Sign out" on the signed-in home page: its menu on wide screens, a button on phones. */
export async function signOutFromHome(page: Page) {
  await expectHome(page);
  const button = page
    .getByRole("button", { name: "Sign out" })
    .filter({ visible: true });
  if ((await button.count()) > 0) {
    await button.first().click();
    return;
  }
  await page.locator('main button[aria-haspopup="menu"]').first().click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
}

export function user(username: string) {
  return testUser(username);
}
