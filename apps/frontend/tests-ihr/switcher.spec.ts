// IndieHR P1-T31e (docs/reviews/p1-t31/design.md §5, attack.md R8, D14): the hospital switcher.
// A person linked to two hospitals sees both in the user menu; choosing the other is a top-level
// sign-in start there, answered by Keycloak single sign-on with no password or code, and no
// cookie or storage crosses over (ADR-009). Signing out at one hospital ends both (Keycloak's
// back-channel logout, P1-T5d: other-tabs.spec.ts). A person with one hospital sees no group.
//
// Needs the real two-address stack (CI job "Browser isolation (Playwright)"): the web app is
// never built on the development machine, so this spec runs in CI only. The people come from the
// Keycloak seed: dr.meera is in both hospitals (care-doctor in each), nurse.asha in alpha only.
import type { BrowserContext, Page } from "@playwright/test";

import { apiJson, facilityIdOf, openSidebarByDefault } from "./lib/desk";
import {
  COOKIE,
  HOSPITALS,
  type Hospital,
  KEYCLOAK,
  expect,
  expectHome,
  expectSignedIn,
  sessionCookieForComparison,
  signIn,
  signOutFromHome,
  test,
  user,
} from "./lib/fixtures";
import {
  appBuster,
  bootSnapshot,
  captureStorageAtLoginBoot,
  leftovers,
  missingPlants,
  newSentinel,
  plant,
  snapshot,
  staffSentinels,
} from "./lib/storage";
import {
  FACILITY_SIGN_OUT,
  GROUP,
  type HospitalRow,
  MY_HOSPITALS_API,
  NOT_SIGNED_IN,
  chooseOther,
  expectGroup,
  openUserMenu,
  usernameOf,
} from "./lib/switcher";

async function hospitalOf(page: Page): Promise<string | undefined> {
  return page.evaluate(async () => {
    const res = await fetch("/api/ihr/auth/session/", {
      headers: { "X-IHR-CSRF": "1" },
    });
    return ((await res.json()) as { hospital?: string }).hospital;
  });
}

/** The facility's overview page (its sidebar holds the user menu). */
async function toOverview(page: Page, hospital: Hospital) {
  const facilityId = await facilityIdOf(page);
  await page.goto(`${HOSPITALS[hospital]}/facility/${facilityId}/overview`);
}

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
  await page.waitForLoadState("load");
  await page.waitForTimeout(2_000);
  expect(
    await leftovers(page, context, sentinel, COOKIE),
    "once settled",
  ).toEqual([]);
}

/** Client-side navigation inside the web app. TanStack Router's history wraps
 * window.history.pushState, so a plain pushState moves the router (P2-T0c; raviger needed a
 * popstate event, which the new router would handle as a second, empty "GO"). */
async function spaNavigate(page: Page, path: string) {
  await page.evaluate((target) => {
    window.history.pushState({}, "", target);
  }, path);
}

test("a member of both hospitals switches by single sign-on, keeps two sessions, and signs out of both (dr.meera)", async ({
  newContext,
}) => {
  const context = await newContext();
  await captureStorageAtLoginBoot(context);
  await openSidebarByDefault(context, HOSPITALS.alpha);
  await openSidebarByDefault(context, HOSPITALS.beta);
  const page = await context.newPage();
  await signIn(page, "alpha", user("dr.meera"));
  const alphaFirst = await sessionCookieForComparison(context, "alpha");
  expect(!!alphaFirst, "alpha session cookie").toBe(true);

  let group = page.locator(GROUP);

  await test.step("1. the menu lists both hospitals; alpha is current, beta can be chosen", async () => {
    await toOverview(page, "alpha");
    const listed = await apiJson<{ hospitals: HospitalRow[] }>(
      page,
      MY_HOSPITALS_API,
    );
    expect(listed.hospitals, "two hospitals listed").toHaveLength(2);
    expect(await openUserMenu(page)).toBe(200);
    group = await expectGroup(page, listed.hospitals);
    const current = listed.hospitals.find((row) => row.current);
    expect(current?.url.startsWith(HOSPITALS.alpha), "current is alpha").toBe(
      true,
    );
  });

  await test.step("2. choosing beta signs in there by single sign-on, with no login form", async () => {
    const seen = await chooseOther(page, group, HOSPITALS.beta);
    await expectSignedIn(page);
    expect(await hospitalOf(page)).toBe("beta");
    expect(seen.committedAtKeycloak, "pages committed at Keycloak").toEqual([]);
    expect(seen.keycloakPages, "Keycloak pages served").toEqual([]);
    for (const field of ["#username", "#password", "#otp"]) {
      await expect(page.locator(field), `${field} never shows`).toHaveCount(0);
    }
  });

  await test.step("3. two different host-only cookies, and nothing of alpha in beta's storage", async () => {
    const alpha = await sessionCookieForComparison(context, "alpha");
    const beta = await sessionCookieForComparison(context, "beta");
    expect(!!(alpha && beta), "both cookies set").toBe(true);
    for (const cookie of [alpha!, beta!]) {
      expect(cookie.domain.startsWith("."), `${cookie.domain} host-only`).toBe(
        false,
      );
      expect(cookie.httpOnly, `${cookie.domain} HttpOnly`).toBe(true);
    }
    expect(alpha!.domain).toBe("alpha.localhost");
    expect(beta!.domain).toBe("beta.localhost");
    // values are compared here and only the booleans asserted
    expect(alpha!.value === beta!.value, "cookies differ").toBe(false);
    expect(
      alpha!.value === alphaFirst!.value,
      "switching left alpha's cookie unchanged",
    ).toBe(true);
    const state = await snapshot(page);
    const text = JSON.stringify(state);
    expect(
      text.includes("alpha.localhost"),
      "an alpha address in beta's storage",
    ).toBe(false);
    expect(
      text.includes(alpha!.value),
      "alpha's cookie in beta's storage",
    ).toBe(false);
  });

  await test.step("4. the menu at beta takes her back to alpha, again with no credentials", async () => {
    await toOverview(page, "beta");
    const listed = await apiJson<{ hospitals: HospitalRow[] }>(
      page,
      MY_HOSPITALS_API,
    );
    await openUserMenu(page);
    group = await expectGroup(page, listed.hospitals);
    expect(
      listed.hospitals
        .find((row) => row.current)
        ?.url.startsWith(HOSPITALS.beta),
      "current is beta",
    ).toBe(true);
    const seen = await chooseOther(page, group, HOSPITALS.alpha);
    await expectSignedIn(page);
    expect(await hospitalOf(page)).toBe("alpha");
    expect(seen.committedAtKeycloak, "pages committed at Keycloak").toEqual([]);
    expect(seen.keycloakPages, "Keycloak pages served").toEqual([]);
    expect(
      !!(await sessionCookieForComparison(context, "beta")),
      "beta's session is still there",
    ).toBe(true);
  });

  await test.step("6. signing out at beta ends alpha too: the list is refused and alpha keeps nothing", async () => {
    // Alpha's own tab: loaded, with values planted that the clean-up owns
    await page.goto(`${HOSPITALS.alpha}/`);
    await expectHome(page);
    await page.waitForLoadState("networkidle");
    const username = await usernameOf(page);
    const sentinel = newSentinel();
    const planted = staffSentinels(sentinel, await appBuster(page));
    await plant(page, planted);
    expect(await missingPlants(page, planted)).toEqual([]);

    const beta = await context.newPage();
    await beta.goto(`${HOSPITALS.beta}/`);
    await signOutFromHome(beta);
    await beta.waitForURL(
      (url) => url.searchParams.get("reason") === "signed_out",
    );
    expect(new URL(beta.url()).origin).not.toBe(KEYCLOAK);

    // Keycloak's back-channel logout reaches alpha well inside the token refresh: the next ask
    // of the list is refused with the not-signed-in body (never a 401: D14, P1-T5d)
    await expect
      .poll(
        async () => {
          try {
            return await page.evaluate(async (path) => {
              const res = await fetch(path, {
                headers: { "X-IHR-CSRF": "1" },
                credentials: "same-origin",
              });
              const body = (await res.json().catch(() => ({}))) as {
                detail?: string;
              };
              return { status: res.status, detail: body.detail };
            }, MY_HOSPITALS_API);
          } catch {
            return { status: -1, detail: undefined };
          }
        },
        { timeout: 10_000, intervals: [500] },
      )
      .toEqual({ status: 403, detail: NOT_SIGNED_IN });

    // The web app's own next call then ends its session and clears what it kept
    await spaNavigate(page, `/users/${username}`);
    await page.waitForURL((url) => url.pathname === "/login");
    expect(new URL(page.url()).searchParams.get("reason")).toBe(
      "session_ended",
    );
    await expectClean(page, context, sentinel);
  });
});

test("a person with one hospital sees no Hospitals group (nurse.asha, alpha only)", async ({
  newContext,
}) => {
  const context = await newContext();
  await openSidebarByDefault(context, HOSPITALS.alpha);
  const page = await context.newPage();
  await signIn(page, "alpha", user("nurse.asha"));
  await toOverview(page, "alpha");
  const listed = await apiJson<{ hospitals: HospitalRow[] }>(
    page,
    MY_HOSPITALS_API,
  );
  expect(listed.hospitals, "one hospital listed").toHaveLength(1);
  // the menu is open and has asked: the group is absent, the rest of the menu is there
  expect(await openUserMenu(page)).toBe(200);
  await expect(
    page.getByRole("menuitem", { name: FACILITY_SIGN_OUT }),
  ).toBeVisible();
  await expect(page.locator(GROUP)).toHaveCount(0);
});
