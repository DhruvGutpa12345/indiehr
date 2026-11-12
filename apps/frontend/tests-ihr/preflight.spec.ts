// IndieHR (P1-T1d): one real sign-in with a computed code before any other spec types one.
// If Keycloak refuses it, the "browser" project does not run (no account gets locked).
import {
  HOSPITALS,
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
