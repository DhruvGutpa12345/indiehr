// IndieHR (P1-T1d, ADR-009): the web app's idle time-out, on Playwright's clock instead of a
// real half-hour wait. The hospital's limit stays at its default (bootstrap: 30 minutes, warning
// 2 minutes before). No input reaches the page while the clock runs, so idle time is real idle
// time to the app: first the warning, then the sign-out with reason=idle, and the server
// session is gone (the server takes the larger of its own and the reported idle time).
import {
  HOSPITALS,
  apiStatus,
  expect,
  expectHome,
  signIn,
  test,
  user,
} from "./lib/fixtures";

test("idle: warning, then signed out with reason=idle (admin.bhavesh at beta)", async ({
  newContext,
}) => {
  const context = await newContext();
  const page = await context.newPage();
  await signIn(page, "beta", user("admin.bhavesh"));

  const limits = await page.evaluate(
    () =>
      (
        window as unknown as {
          __IHR_AUTH__?: { idle_seconds: number; warn_seconds: number };
        }
      ).__IHR_AUTH__,
  );
  expect(limits, "idle settings from /api/ihr/bootstrap.js").toBeTruthy();
  const { idle_seconds: idle, warn_seconds: warn } = limits!;

  await page.clock.install();
  await page.goto(`${HOSPITALS.beta}/`);
  await expectHome(page);

  // Into the warning period (the app checks every 10 s)
  await page.clock.fastForward((idle - warn + 30) * 1000);
  await expect(
    page.getByText(/You will be signed out in \d+ minutes? without activity\./),
  ).toBeVisible();
  expect(new URL(page.url()).pathname).toBe("/");

  // Past the limit
  await page.clock.fastForward((warn + 30) * 1000);
  await page.waitForURL((url) => url.pathname === "/login");
  expect(new URL(page.url()).searchParams.get("reason")).toBe("idle");
  await expect(page.getByRole("status")).toHaveText(
    "You were signed out because there was no activity for a while.",
  );
  expect(await apiStatus(page, "/api/ihr/auth/session/")).toBe(403);
});
