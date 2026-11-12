// IndieHR (P1-T1d, TENANCY_DESIGN §11.4): a sign-in at one hospital is worth nothing at the
// other. Keycloak's own membership gate stops a non-member (she never reaches our "not
// linked" page, which ihr_identity's tests cover), a copied cookie is refused, and a member of
// both hospitals holds two independent host-only cookies.
import type { Request } from "@playwright/test";

import {
  COOKIE,
  HOSPITALS,
  KEYCLOAK,
  NOT_MEMBER,
  SIGN_IN_BUTTON,
  apiStatus,
  expect,
  hasSessionCookie,
  sessionCookieForComparison,
  signIn,
  signInBySso,
  test,
  user,
} from "./lib/fixtures";

test("a non-member is stopped at the other hospital, and her cookie is refused there (nurse.asha)", async ({
  newContext,
}) => {
  const context = await newContext();
  const alpha = await context.newPage();
  await signIn(alpha, "alpha", user("nurse.asha"));
  const alphaCookie = await sessionCookieForComparison(context, "alpha");
  expect(!!alphaCookie, "alpha session cookie").toBe(true);

  await test.step("Keycloak's membership gate stops her at beta", async () => {
    const beta = await context.newPage();
    await beta.goto(`${HOSPITALS.beta}/login`);
    await beta.getByRole("button", { name: SIGN_IN_BUTTON }).click();
    await beta.waitForURL((url) => url.origin === KEYCLOAK);
    await expect(beta.getByText(NOT_MEMBER)).toBeVisible();
    expect(new URL(beta.url()).origin).toBe(KEYCLOAK);
    expect(
      await hasSessionCookie(context, "beta"),
      "no ihr_session at beta",
    ).toBe(false);
    // her alpha session is untouched
    expect(await apiStatus(alpha, "/api/ihr/auth/session/")).toBe(200);
  });

  await test.step("her alpha cookie, copied to beta's address, is refused", async () => {
    const attacker = await newContext();
    const page = await attacker.newPage();
    await page.goto(`${HOSPITALS.beta}/login`);
    await attacker.addCookies([
      {
        name: COOKIE,
        value: alphaCookie!.value,
        domain: "beta.localhost",
        path: "/",
        httpOnly: true,
        sameSite: "Lax",
      },
    ]);
    expect(await apiStatus(page, "/api/ihr/auth/session/")).toBe(403);
    expect(await apiStatus(page, "/api/v1/users/getcurrentuser/")).toBe(403);
    // and it still works where it belongs
    expect(await apiStatus(alpha, "/api/ihr/auth/session/")).toBe(200);
  });
});

test("a member of both hospitals holds two independent host-only cookies (dr.meera)", async ({
  newContext,
}) => {
  const context = await newContext();
  const requests: Request[] = [];
  context.on("request", (request) => requests.push(request));

  const alpha = await context.newPage();
  await signIn(alpha, "alpha", user("dr.meera"));
  const alphaBefore = await sessionCookieForComparison(context, "alpha");

  const beta = await context.newPage();
  await signInBySso(beta, "beta");
  const alphaAfter = await sessionCookieForComparison(context, "alpha");
  const betaCookie = await sessionCookieForComparison(context, "beta");

  // Cookie values are compared here and asserted as booleans: never printed
  expect(!!(alphaBefore && alphaAfter && betaCookie), "both cookies set").toBe(
    true,
  );
  for (const cookie of [alphaAfter!, betaCookie!]) {
    // Host-only: no leading dot, so the cookie goes to its own address only
    expect(cookie.domain.startsWith("."), `${cookie.domain} host-only`).toBe(
      false,
    );
    expect(cookie.httpOnly, `${cookie.domain} HttpOnly`).toBe(true);
  }
  expect(alphaAfter!.domain).toBe("alpha.localhost");
  expect(betaCookie!.domain).toBe("beta.localhost");
  expect(alphaAfter!.value === betaCookie!.value, "cookies differ").toBe(false);
  expect(
    alphaAfter!.value === alphaBefore!.value,
    "signing in at beta left alpha's cookie unchanged",
  ).toBe(true);

  // Each session answers for its own hospital only
  const who = (page: typeof alpha) =>
    page.evaluate(async () => {
      const res = await fetch("/api/ihr/auth/session/", {
        headers: { "X-IHR-CSRF": "1" },
      });
      return ((await res.json()) as { hospital?: string }).hospital;
    });
  expect(await who(alpha)).toBe("alpha");
  expect(await who(beta)).toBe("beta");

  // No request to either address carried the other's cookie, and Keycloak got neither
  const leaks: string[] = [];
  for (const request of requests) {
    const host = new URL(request.url()).host;
    const cookies = (await request.allHeaders())["cookie"] ?? "";
    const carriesAlpha = cookies.includes(alphaAfter!.value);
    const carriesBeta = cookies.includes(betaCookie!.value);
    if (host.startsWith("alpha.") && carriesBeta)
      leaks.push(`beta cookie sent to ${host}`);
    if (host.startsWith("beta.") && carriesAlpha)
      leaks.push(`alpha cookie sent to ${host}`);
    if (host.startsWith("auth.") && (carriesAlpha || carriesBeta))
      leaks.push(`hospital cookie sent to ${host}`);
  }
  expect(leaks).toEqual([]);
  expect(requests.length).toBeGreaterThan(10);
});
