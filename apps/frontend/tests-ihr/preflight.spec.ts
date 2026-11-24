// IndieHR (P1-T1d): one real sign-in with a computed code before any other spec types one.
// If Keycloak refuses it, the "browser" project does not run (no account gets locked).
//
// P2-T0c (docs/reviews/p2-t0c/design.md §5.2, R7, R8): after the first sign-in, two checks of the
// router port that every later spec relies on. They run here so a broken sign-in return or an
// auth page that shows CARE's own form stops the whole run: (1) /2fa, /forgot-password and
// /password_reset/<token> end on /login; (2) a signed-out staff address returns to itself after
// sign-in, through /login?redirect=<absolute URL>.
import { facilityIdOf } from "./lib/desk";
import {
  HOSPITALS,
  SIGN_IN_BUTTON,
  apiStatus,
  expect,
  signIn,
  test,
  user,
} from "./lib/fixtures";

test("Keycloak accepts our one-time code (nurse.asha at alpha)", async ({
  newContext,
}) => {
  const page = await (await newContext()).newPage();
  await signIn(page, "alpha", user("nurse.asha"));
  expect(new URL(page.url()).origin).toBe(HOSPITALS.alpha);
  const session = await page.evaluate(async () => {
    const res = await fetch("/api/ihr/auth/session/", {
      headers: { "X-IHR-CSRF": "1" },
    });
    return (await res.json()) as { username?: string; second_factor?: boolean };
  });
  expect(session.second_factor).toBe(true);
  expect(await apiStatus(page, "/api/v1/users/getcurrentuser/")).toBe(200);
});

// CARE's own pages for a password, a second factor or a reset would ask for secrets Keycloak
// owns; our router sends each address to the sign-in page (src/components/ihr/ihrRouteTree.tsx)
for (const address of [
  "/2fa",
  "/forgot-password",
  "/password_reset/sentinel",
]) {
  test(`${address} ends on the sign-in page (signed out)`, async ({
    newContext,
  }) => {
    const page = await (await newContext()).newPage();
    await page.goto(`${HOSPITALS.alpha}${address}`);
    await page.waitForURL((url) => url.pathname === "/login");
    await expect(
      page.getByRole("button", { name: SIGN_IN_BUTTON }),
    ).toBeVisible();
    // no password or code field of CARE's: the sign-in is Keycloak's
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
  });
}

test("a signed-out staff address returns to itself after sign-in (nurse.asha at alpha)", async ({
  newContext,
}) => {
  // The facility's id, from another context (a Keycloak session of its own)
  const known = await (await newContext()).newPage();
  await signIn(known, "alpha", user("dr.meera"));
  const facilityId = await facilityIdOf(known);
  const wanted = `/facility/${facilityId}/patients`;

  const page = await (await newContext()).newPage();
  await signIn(page, "alpha", user("nurse.asha"), {
    from: `${HOSPITALS.alpha}${wanted}`,
    onSignInPage: async (signInPage) => {
      // The app sends a signed-out visitor to /login and names the way back: the whole address,
      // which only the sign-in's own start (safeNext) may turn into a path on this origin
      await signInPage.waitForURL((url) => url.pathname === "/login");
      const redirect = new URL(signInPage.url()).searchParams.get("redirect");
      expect(redirect, "redirect parameter of /login").toBe(
        `${HOSPITALS.alpha}${wanted}`,
      );
    },
  });
  await page.waitForURL((url) => url.pathname === wanted);
  const back = new URL(page.url());
  expect(back.origin).toBe(HOSPITALS.alpha);
  expect(back.pathname + back.search).toBe(wanted);
});
