// IndieHR P1-T24b (docs/reviews/p1-t24/design.md "T24b as decided", attack.md "Decision" A1, G2,
// A4, Q-K113 B, Q-K118 A): the staff "Sign-in" card on a user page.
//
//   a hospital-wide admin (admin.priya, alpha) opens a colleague's page -> the card says the
//   colleague is active staff and offers "Reset sign-in" -> the confirm dialog carries the
//   warning text -> "Cancel" sends NOTHING (a reset wipes credentials and mails the person, and
//   is limited to one a day, so the spec never confirms it)
//   the colleague's own page (and a service-less look at oneself) has no card and asks nothing
//   a facility-only admin (admin.anil, alpha: Facility Admin at the facility root only) opens the
//   same colleague -> no card, no reset button, and a reset typed straight at the API is 403
//   a front-desk role (reception.ravi) never has the card and never asks the staff API.
//
// - Nothing here resets a real sign-in: the reset is only ever cancelled or refused.
// - Dead-end listener (lib/deadEnds.ts): the one refusal this spec provokes is declared.
// - Matches on text use .filter({ visible: true }).first(); data-ihr attributes are ours.
// - Waits are for the API answer (waitForResponse), never network idle.
import type { Page } from "@playwright/test";

import { watchDeadEnds } from "./lib/deadEnds";
import { facilityIdOf, openSidebarByDefault } from "./lib/desk";
import { HOSPITALS, expect, signIn, test, user } from "./lib/fixtures";

// the CARE usernames behind the seeded Keycloak logins (seeding.py, scripts/care-dev.sh)
const COLLEAGUE = "care-doctor"; // dr.meera
const STAFF_API = /^\/api\/ihr\/staff\//;
const WARNING =
  "This removes their password and authenticator and signs them out at every hospital. " +
  "They get a link at their own e-mail. Check who is asking, in person or on a known phone " +
  "number, before you continue.";

function isAccessGet(username: string) {
  return (response: { url(): string; request(): { method(): string } }) =>
    new URL(response.url()).pathname === `/api/ihr/staff/${username}/access/` &&
    response.request().method() === "GET";
}

/** Requests to the staff API made from now on (method and path only). */
function staffRequests(page: Page): string[] {
  const seen: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (STAFF_API.test(url.pathname))
      seen.push(`${request.method()} ${url.pathname}`);
  });
  return seen;
}

test("the colleague's own page has no card (and links her sign-in for the next tests)", async ({
  newContext,
}) => {
  const origin = HOSPITALS.alpha;
  const context = await newContext();
  await openSidebarByDefault(context, origin);
  const page = await context.newPage();
  await signIn(page, "alpha", user("dr.meera"));
  const facilityId = await facilityIdOf(page);
  const asked = staffRequests(page);

  await page.goto(`${origin}/facility/${facilityId}/users/${COLLEAGUE}`);
  await expect(
    page.getByText("Personal Information").filter({ visible: true }).first(),
  ).toBeVisible();
  await expect(page.locator('[data-ihr="staff-card"]')).toHaveCount(0);
  expect(asked, "staff API calls on one's own page").toEqual([]);
});

test("a hospital-wide admin sees the card and the reset warning (admin.priya, alpha)", async ({
  newContext,
}) => {
  const origin = HOSPITALS.alpha;
  const context = await newContext();
  await openSidebarByDefault(context, origin);
  const page = await context.newPage();
  await signIn(page, "alpha", user("admin.priya"));
  const facilityId = await facilityIdOf(page);
  const dead = watchDeadEnds(page);
  const asked = staffRequests(page);

  const access = page.waitForResponse(isAccessGet(COLLEAGUE));
  await page.goto(`${origin}/facility/${facilityId}/users/${COLLEAGUE}`);
  const answer = await access;
  expect(answer.status()).toBe(200);
  const body = (await answer.json()) as { state: string; offers: string[] };

  await test.step("the card says active staff and offers a reset (and only that)", async () => {
    expect(body.state).toBe("active");
    expect(body.offers).toEqual(["reset-sign-in"]);
    const card = page.locator('[data-ihr="staff-card"]');
    await expect(card).toBeVisible();
    await expect(card).toContainText("Sign-in");
    await expect(page.locator('[data-ihr="staff-state"]')).toContainText(
      "Active staff",
    );
    await expect(
      page.locator('[data-ihr="staff-offer-reset-sign-in"]'),
    ).toBeVisible();
    await expect(
      page.locator('[data-ihr="staff-offer-resend-invitation"]'),
    ).toHaveCount(0);
    await expect(
      page.locator('[data-ihr="staff-offer-cancel-invitation"]'),
    ).toHaveCount(0);
    dead.expectNone("on a colleague's page");
  });

  await test.step("the confirm carries the warning; Cancel sends nothing", async () => {
    await page.locator('[data-ihr="staff-offer-reset-sign-in"]').click();
    const dialog = page.locator('[data-ihr="staff-confirm"]');
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(WARNING);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toHaveCount(0);
    // the card is still the same, and no request other than the one read was made
    await expect(page.locator('[data-ihr="staff-card"]')).toBeVisible();
    await expect(page.locator('[data-ihr="staff-notice"]')).toHaveCount(0);
    expect(
      asked.filter((line) => line.startsWith("POST")),
      "POSTs to the staff API after Cancel",
    ).toEqual([]);
    dead.expectNone("after cancelling the reset");
  });

  await test.step("her own page has no card and asks nothing", async () => {
    asked.length = 0;
    await page.goto(`${origin}/facility/${facilityId}/users/care-admin`);
    await expect(
      page.getByText("Personal Information").filter({ visible: true }).first(),
    ).toBeVisible();
    await expect(page.locator('[data-ihr="staff-card"]')).toHaveCount(0);
    expect(asked, "staff API calls on one's own page").toEqual([]);
  });
});

test("a facility-only admin does not get the reset (admin.anil, alpha)", async ({
  newContext,
}) => {
  const origin = HOSPITALS.alpha;
  const context = await newContext();
  await openSidebarByDefault(context, origin);
  const page = await context.newPage();
  await signIn(page, "alpha", user("admin.anil"));
  const facilityId = await facilityIdOf(page);
  const dead = watchDeadEnds(page);

  const access = page.waitForResponse(isAccessGet(COLLEAGUE));
  await page.goto(`${origin}/facility/${facilityId}/users/${COLLEAGUE}`);
  const answer = await access;
  expect(answer.status()).toBe(200);
  const body = (await answer.json()) as { offers: string[] };

  await test.step("the backend offers nothing, so there is no card and no button", async () => {
    expect(body.offers).toEqual([]);
    await expect(
      page.getByText("Personal Information").filter({ visible: true }).first(),
    ).toBeVisible();
    await expect(page.locator('[data-ihr="staff-card"]')).toHaveCount(0);
    await expect(
      page.locator('[data-ihr="staff-offer-reset-sign-in"]'),
    ).toHaveCount(0);
    dead.expectNone("on a colleague's page");
  });

  await test.step("a reset typed straight at the API is refused (403), before anything is said", async () => {
    dead.declare({
      method: "POST",
      path: /^\/api\/ihr\/staff\/care-doctor\/reset-sign-in\/$/,
      status: 403,
      reason: "A1: a facility-only admin may not reset a sign-in",
    });
    const status = await page.evaluate(async (username) => {
      const res = await fetch(`/api/ihr/staff/${username}/reset-sign-in/`, {
        method: "POST",
        headers: { "X-IHR-CSRF": "1" },
        credentials: "same-origin",
      });
      return res.status;
    }, COLLEAGUE);
    expect(status).toBe(403);
    dead.expectNone("after the refused reset");
  });
});

test("a front-desk role never has the card and never asks the staff API (reception.ravi)", async ({
  newContext,
}) => {
  const origin = HOSPITALS.alpha;
  const context = await newContext();
  await openSidebarByDefault(context, origin);
  const page = await context.newPage();
  await signIn(page, "alpha", user("reception.ravi"));
  const facilityId = await facilityIdOf(page);
  const asked = staffRequests(page);

  // a positive wait first: the page asked for the user, whatever the answer was
  const userCall = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === `/api/v1/users/${COLLEAGUE}/`,
  );
  await page.goto(`${origin}/facility/${facilityId}/users/${COLLEAGUE}`);
  await userCall;
  await page.waitForTimeout(1_000);
  await expect(page.locator('[data-ihr="staff-card"]')).toHaveCount(0);
  expect(asked, "staff API calls by a restricted role").toEqual([]);
});
