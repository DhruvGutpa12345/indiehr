// IndieHR (P1-T1d): the localStorage key "user" holds the practitioner a *patient* picked in
// the public booking flow (name and username, types/scheduling/schedule.ts). It is cleared on
// an explicit sign-out: the patient's own (usePatientSignOut, our patch, checked here) and any
// staff session end (clearStaffStorage). A patient session that lapses or is abandoned keeps
// it, as it keeps care_patient_token and selectedPatient (upstream behaviour; known gap,
// docs/reviews/p1-t1d/design.md "After the code review").
// No real patient sign-in is needed: the patient portal only checks that a token is present
// and recent, and the sign-out under test is purely in the browser.
// Needs the P1-T1d patch (hooks/usePatientSignOut.ts): bundles built before it fail.
import { HOSPITALS, expect, test } from "./lib/fixtures";
import { newSentinel } from "./lib/storage";

test("patient sign-out clears the booking 'user' key [needs P1-T1d bundle]", async ({
  newContext,
}) => {
  const context = await newContext();
  const page = await context.newPage();
  await page.goto(`${HOSPITALS.alpha}/login`);
  const sentinel = newSentinel();
  await page.evaluate((value) => {
    localStorage.setItem(
      "care_patient_token",
      JSON.stringify({
        token: `${value}-not-a-token`,
        phoneNumber: "+910000000000",
        createdAt: new Date().toISOString(),
      }),
    );
    localStorage.setItem(
      "user",
      JSON.stringify({ username: value, first_name: value, last_name: value }),
    );
    localStorage.setItem("selectedPatient", JSON.stringify({ id: value }));
  }, sentinel);

  await page.goto(`${HOSPITALS.alpha}/`);
  const header = page.locator("header").first();
  await expect(header.getByRole("button", { name: "Home" })).toBeVisible();
  await header.getByRole("button").filter({ hasNotText: "Home" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await page.waitForURL((url) => url.pathname === "/login");

  const left = await page.evaluate(() => ({
    token: localStorage.getItem("care_patient_token"),
    user: localStorage.getItem("user"),
    selectedPatient: localStorage.getItem("selectedPatient"),
  }));
  expect(left).toEqual({ token: null, user: null, selectedPatient: null });
});
