// IndieHR (P1-T1d, ADR-009): a sign-out reaches the person's other tabs and other hospitals.
// - Another tab of the same hospital ends at once (BroadcastChannel), with reason=signed_out
//   and without sending a single API request of its own first.
// - A sign-out at the other hospital (Keycloak's back-channel logout) makes this hospital's
//   very next API call fail, well inside the one-minute token refresh, so this cannot pass on
//   a time-out: the app then lands on reason=session_ended.
// Both tabs that end are then checked for leftovers (lib/storage.ts): the second tab's own
// sessionStorage, and, on the server-ended path (endExpiredSession), everything planted in
// alpha, at the start of its /login load and after it.
import type { BrowserContext, Page } from "@playwright/test";

import {
  COOKIE,
  HOSPITALS,
  apiStatus,
  expect,
  expectHome,
  signIn,
  signInBySso,
  signOutFromHome,
  test,
  user,
} from "./lib/fixtures";
import {
  appBuster,
  bookingUserSentinel,
  bootSnapshot,
  captureStorageAtLoginBoot,
  leftovers,
  missingPlants,
  newSentinel,
  plant,
  staffSentinels,
} from "./lib/storage";

/** No leftovers at the start of this /login load, when it shows, and once it settled. */
async function expectClean(
  page: Page,
  context: BrowserContext,
  sentinel: string,
) {
  const copy = await bootSnapshot(page);
  expect(
    await leftovers(page, context, sentinel, COOKIE, copy),
    "at the start of the /login load",
  ).toEqual([]);
  expect(await leftovers(page, context, sentinel, COOKIE), "on /login").toEqual(
    [],
  );
  // "load" plus 2 s, not "networkidle": a tab sent to /login by another tab's message never
  // reached networkidle locally (a request of the old document is still counted as open)
  await page.waitForLoadState("load");
  await page.waitForTimeout(2_000);
  expect(
    await leftovers(page, context, sentinel, COOKIE),
    "once settled",
  ).toEqual([]);
}

/** Client-side navigation inside the web app (raviger listens to popstate). */
async function spaNavigate(page: Page, path: string) {
  await page.evaluate((target) => {
    window.history.pushState({}, "", target);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, path);
}

test("a second tab of the same hospital ends at once, with no API request (nurse.asha)", async ({
  newContext,
}) => {
  const context = await newContext();
  await captureStorageAtLoginBoot(context);
  const first = await context.newPage();
  await signIn(first, "alpha", user("nurse.asha"));
  await first.goto(`${HOSPITALS.alpha}/`);
  await expectHome(first);
  const second = await context.newPage();
  await second.goto(`${HOSPITALS.alpha}/`);
  await expectHome(second);
  await second.waitForLoadState("networkidle");
  // The second tab's own sessionStorage (per tab): only its own handler can clear it
  const sentinel = newSentinel();
  const secondsOwn = { session: staffSentinels(sentinel).session };
  await plant(second, secondsOwn);
  expect(await missingPlants(second, secondsOwn)).toEqual([]);

  let watching = false;
  let leftPage = false;
  const apiCalls: string[] = [];
  second.on("framenavigated", (frame) => {
    // The full-page load of /login?reason=… (a same-document history change does not count)
    if (
      watching &&
      frame === second.mainFrame() &&
      new URL(frame.url()).pathname === "/login"
    )
      leftPage = true;
  });
  second.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (watching && !leftPage && path.startsWith("/api/")) apiCalls.push(path);
  });

  watching = true;
  await signOutFromHome(first);
  await second.waitForURL((url) => url.pathname === "/login");
  expect(new URL(second.url()).searchParams.get("reason")).toBe("signed_out");
  expect(
    apiCalls,
    "API requests sent by the second tab before it left",
  ).toEqual([]);
  await expect(second.getByRole("status")).toHaveText(
    "You have signed out of all hospitals.",
  );
  expect(await apiStatus(second, "/api/ihr/auth/session/")).toBe(403);
  await test.step("the second tab's own storage is clean", async () => {
    await expectClean(second, context, sentinel);
  });
});

// The final clean-up step needs the P1-T1d patch (clearStaffStorage also removes the patient
// portal's token and the booking "user" key): bundles built before it fail there only.
test("sign-out at the other hospital refuses this one's next API call, and alpha keeps nothing (dr.meera) [clean-up needs P1-T1d bundle]", async ({
  newContext,
}) => {
  const context = await newContext();
  await captureStorageAtLoginBoot(context);
  const alpha = await context.newPage();
  await signIn(alpha, "alpha", user("dr.meera"));
  const beta = await context.newPage();
  await signInBySso(beta, "beta");
  await alpha.goto(`${HOSPITALS.alpha}/`);
  await expectHome(alpha);
  const username = await alpha.evaluate(async () => {
    const res = await fetch("/api/ihr/auth/session/", {
      headers: { "X-IHR-CSRF": "1" },
    });
    return ((await res.json()) as { username: string }).username;
  });
  await alpha.waitForLoadState("networkidle");
  // Alpha is this context's only alpha tab, so whatever clean-up happens is
  // endExpiredSession's own (no BroadcastChannel peer at the same address)
  const sentinel = newSentinel();
  const planted = staffSentinels(sentinel, await appBuster(alpha));
  planted.local = { ...planted.local, ...bookingUserSentinel(sentinel).local };
  await plant(alpha, planted);
  expect(await missingPlants(alpha, planted)).toEqual([]);

  await beta.goto(`${HOSPITALS.beta}/`);
  await signOutFromHome(beta);
  await beta.waitForURL(
    (url) => url.searchParams.get("reason") === "signed_out",
  );
  const signedOutAt = Date.now();

  // One API call by the web app at alpha, a few seconds later
  const refused = alpha.waitForResponse(
    (response) =>
      new URL(response.url()).pathname.startsWith("/api/") &&
      response.status() === 403,
  );
  await spaNavigate(alpha, `/users/${username}`);
  await refused;
  expect(
    Date.now() - signedOutAt,
    "refused within about 10 s of the sign-out at beta",
  ).toBeLessThan(10_000);
  await alpha.waitForURL((url) => url.pathname === "/login");
  expect(new URL(alpha.url()).searchParams.get("reason")).toBe("session_ended");
  await expect(alpha.getByRole("status")).toHaveText(
    "Your session has ended. Please sign in again.",
  );
  await test.step("alpha keeps nothing after the server ended its session", async () => {
    await expectClean(alpha, context, sentinel);
  });
});
