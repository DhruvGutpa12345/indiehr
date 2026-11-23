// IndieHR P1-T31e (docs/reviews/p1-t31/design.md §5, attack.md R8): the "Hospitals" group of the
// staff user menu, as the browser specs drive it. Shared by switcher.spec.ts and roles-menus.spec.ts.
import type { Locator, Page } from "@playwright/test";

import { KEYCLOAK, expect } from "./fixtures";

export const MY_HOSPITALS_API = "/api/ihr/my-hospitals/";
export const GROUP = '[data-testid="ihr-hospitals-menu"]';
export const NOT_SIGNED_IN = "Authentication credentials were not provided.";
/** The facility user menu's sign-out item is CARE's `logout` string ("Log Out"); the patient
 * and home pages say "Sign out" (lib/fixtures.ts). */
export const FACILITY_SIGN_OUT = "Log Out";

export interface HospitalRow {
  name: string;
  url: string;
  current: boolean;
  available: boolean;
}

/** The signed-in user's CARE username (the user menu's trigger shows it). */
export async function usernameOf(page: Page): Promise<string> {
  return page.evaluate(async () => {
    const res = await fetch("/api/ihr/auth/session/", {
      headers: { "X-IHR-CSRF": "1" },
      credentials: "same-origin",
    });
    return ((await res.json()) as { username: string }).username;
  });
}

/**
 * Opens the facility sidebar's user menu (the sidebar is expanded by the `sidebar:state` cookie,
 * so the trigger shows the username). The group's list is fetched when the menu opens, so the
 * answer is waited for: nothing is asserted on an empty menu that has not asked yet.
 */
export async function openUserMenu(page: Page): Promise<number> {
  const username = await usernameOf(page);
  const asked = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === MY_HOSPITALS_API &&
      response.request().method() === "GET",
  );
  await page
    .locator('button[aria-haspopup="menu"]')
    .filter({ visible: true })
    .filter({ hasText: username })
    .first()
    .click();
  const answer = await asked;
  await expect(
    page.getByRole("menuitem", { name: FACILITY_SIGN_OUT }),
  ).toBeVisible();
  return answer.status();
}

/** The group, with one row per hospital whose kind and name match the API's answer. */
export async function expectGroup(
  page: Page,
  listed: HospitalRow[],
): Promise<Locator> {
  const group = page.locator(GROUP);
  await expect(group).toBeVisible();
  const items = group.locator("[data-ihr-hospital]");
  await expect(items).toHaveCount(listed.length);
  for (const row of listed) {
    const kind = row.current
      ? "current"
      : row.available
        ? "other"
        : "unavailable";
    const item = items.filter({ hasText: row.name });
    await expect(item, `"${row.name}" is listed once`).toHaveCount(1);
    await expect(item).toHaveAttribute("data-ihr-hospital", kind);
    if (kind === "other") {
      await expect(item).not.toHaveAttribute("aria-disabled", "true");
    } else {
      await expect(item).toHaveAttribute("aria-disabled", "true");
    }
  }
  // the current hospital comes first
  await expect(items.first()).toHaveAttribute("data-ihr-hospital", "current");
  return group;
}

/**
 * Chooses the other hospital in the open menu and waits for the single sign-on round trip to
 * end signed in there. The page is watched all the way: no committed navigation lands on
 * Keycloak, and Keycloak never answers a navigation with a page (its login form), so no
 * password or code could have been asked for. Returns what was seen, for the caller to assert.
 */
export async function chooseOther(
  page: Page,
  group: Locator,
  targetOrigin: string,
) {
  const navigations: string[] = [];
  const keycloakPages: string[] = [];
  const onNavigated = (frame: ReturnType<Page["mainFrame"]>) => {
    if (frame === page.mainFrame()) navigations.push(frame.url());
  };
  const onResponse = (response: import("@playwright/test").Response) => {
    const request = response.request();
    if (
      request.isNavigationRequest() &&
      new URL(response.url()).origin === KEYCLOAK &&
      response.status() === 200
    )
      keycloakPages.push(new URL(response.url()).pathname);
  };
  page.on("framenavigated", onNavigated);
  page.on("response", onResponse);
  try {
    const left = page.waitForRequest(
      (request) =>
        request.isNavigationRequest() &&
        new URL(request.url()).origin === targetOrigin &&
        new URL(request.url()).pathname === "/api/ihr/auth/login/",
      { timeout: 30_000 },
    );
    await group.locator('[data-ihr-hospital="other"]').first().click();
    await left;
    await page.waitForURL(
      (url) =>
        url.origin === targetOrigin &&
        !url.pathname.startsWith("/api/") &&
        (url.pathname !== "/login" || url.searchParams.has("error")),
      { timeout: 30_000 },
    );
    const error = new URL(page.url()).searchParams.get("error");
    if (error) throw new Error(`the switch ended with error=${error}`);
  } finally {
    page.off("framenavigated", onNavigated);
    page.off("response", onResponse);
  }
  return {
    navigations,
    keycloakPages,
    committedAtKeycloak: navigations.filter(
      (url) => new URL(url).origin === KEYCLOAK,
    ),
  };
}
