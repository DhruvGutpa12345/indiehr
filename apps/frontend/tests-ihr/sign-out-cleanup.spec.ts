// IndieHR (P1-T1d, Q-T9): after a staff member signs out, the browser holds nothing of them.
// Sentinels are planted under every key the clean-up owns (the persisted query cache as one the
// app would restore: its own buster, a dehydrated query marked to persist). After sign-out no
// sentinel may be left in any storage or cookie, the persisted query cache and the stored user
// must be empty, and any other key must be on the reviewed allow-list (lib/storage.ts).
// Checked three times: on the storage copied at the very start of the /login load, before the
// sign-in page's own start-up code could remove anything (it drops CARE's old tokens and a
// cache with another buster); when the "signed out" notice shows; and once the page settled.
import {
  COOKIE,
  HOSPITALS,
  apiStatus,
  expect,
  expectHome,
  hasSessionCookie,
  signIn,
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

const SIGNED_OUT = "You have signed out of all hospitals.";

test("staff sign-out leaves no sentinel and only reviewed keys (admin.bhavesh at beta)", async ({
  newContext,
}) => {
  const context = await newContext();
  await captureStorageAtLoginBoot(context);
  const page = await context.newPage();
  await signIn(page, "beta", user("admin.bhavesh"));
  await page.goto(`${HOSPITALS.beta}/`);
  await expectHome(page);
  await page.waitForLoadState("networkidle");

  const sentinel = newSentinel();
  const planted = staffSentinels(sentinel, await appBuster(page));
  await plant(page, planted);
  // Nothing (e.g. the app's own cache persister) replaced a plant before the sign-out
  expect(await missingPlants(page, planted)).toEqual([]);

  await signOutFromHome(page);
  await page.waitForURL(
    (url) =>
      url.pathname === "/login" &&
      url.searchParams.get("reason") === "signed_out",
  );

  await test.step("clean before the sign-in page's own code ran", async () => {
    const copy = await bootSnapshot(page);
    expect(await leftovers(page, context, sentinel, COOKIE, copy)).toEqual([]);
  });
  await expect(page.getByRole("status")).toHaveText(SIGNED_OUT);
  await test.step("clean when the notice shows", async () => {
    expect(await leftovers(page, context, sentinel, COOKIE)).toEqual([]);
  });
  await test.step("still clean once the page has settled", async () => {
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);
    expect(await leftovers(page, context, sentinel, COOKIE)).toEqual([]);
  });
  expect(
    await hasSessionCookie(context, "beta"),
    "no ihr_session at beta",
  ).toBe(false);
  expect(await apiStatus(page, "/api/ihr/auth/session/")).toBe(403);
});

// Needs the P1-T1d patch (clearStaffStorage, Utils/ihr/staffSession.ts): bundles built before
// it fail.
test("staff sign-out also clears the patient booking 'user' key [needs P1-T1d bundle]", async ({
  newContext,
}) => {
  const context = await newContext();
  await captureStorageAtLoginBoot(context);
  const page = await context.newPage();
  await signIn(page, "alpha", user("nurse.asha"));
  await page.goto(`${HOSPITALS.alpha}/`);
  const sentinel = newSentinel();
  await plant(page, bookingUserSentinel(sentinel));
  await signOutFromHome(page);
  await page.waitForURL(
    (url) => url.searchParams.get("reason") === "signed_out",
  );
  const copy = await bootSnapshot(page);
  expect(
    copy.local["user"] ?? null,
    "'user' at the start of /login",
  ).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem("user"))).toBeNull();
  expect(await leftovers(page, context, sentinel, COOKIE)).toEqual([]);
});
