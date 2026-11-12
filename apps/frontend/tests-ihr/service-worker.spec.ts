// IndieHR (P1-T1d): the web app's service worker precaches the static build only (Workbox
// injectManifest, vite.config.mts). Guard: after browsing signed-in screens under its control,
// every cache is Workbox's precache and none holds an API reply, so no patient or staff data
// is ever kept in Cache Storage (which the sign-out clean-up does not touch).
import {
  HOSPITALS,
  expect,
  expectHome,
  signIn,
  test,
  user,
} from "./lib/fixtures";
import { apiCacheProblems, snapshot } from "./lib/storage";

test("the service worker caches no API responses (dr.meera at alpha)", async ({
  newContext,
}) => {
  const context = await newContext();
  const page = await context.newPage();
  await signIn(page, "alpha", user("dr.meera"));
  await page.goto(`${HOSPITALS.alpha}/`);
  await expectHome(page);
  const active = await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    return Boolean(registration.active);
  });
  expect(active, "service worker active").toBe(true);

  // Load again so the worker controls the page, then browse signed-in screens
  await page.reload();
  await expectHome(page);
  expect(
    await page.evaluate(() => Boolean(navigator.serviceWorker.controller)),
    "page controlled by the service worker",
  ).toBe(true);
  await page.getByText("View facility details").first().click();
  await page.waitForURL((url) => url.pathname.startsWith("/facility/"));
  await page.waitForLoadState("networkidle");
  await page.goBack();
  await expectHome(page);
  await page.waitForLoadState("networkidle");

  const state = await snapshot(page);
  const names = Object.keys(state.caches);
  expect(names.length, "the precache exists").toBeGreaterThan(0);
  expect(apiCacheProblems(state.caches)).toEqual([]);
});
