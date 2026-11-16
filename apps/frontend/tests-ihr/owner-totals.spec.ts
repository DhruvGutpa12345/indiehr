// IndieHR P1-T11c3 (docs/reviews/p1-t11/design-c.md §4 c3, §7, §13.5): the Owner's hospital
// totals, signed in as `owner.vikram` (alpha).
//
//   this month's totals load -> the tiles show rupees (lakh or crore words, or the exact figure
//   when small) with the exact figure as their title -> a 400-day range shows its message and
//   sends NO request -> the menu has no patient search -> a good range is kept in the address
//   -> Back shows the first range's figures under the first range's dates (boxes and the
//   answer's own range heading, review F5).
//
// - Dead-end listener (lib/deadEnds.ts): no /api/ answer 400, 403, 404 or 5xx.
// - Matches on text use .filter({ visible: true }).first(); data-ihr attributes are ours.
import { watchDeadEnds } from "./lib/deadEnds";
import { facilityIdOf, openSidebarByDefault } from "./lib/desk";
import { HOSPITALS, expect, signIn, test, user } from "./lib/fixtures";

const TOTALS_API = "/api/ihr/owner/totals/";

test("the Owner's hospital totals (owner.vikram, alpha)", async ({
  newContext,
}) => {
  const origin = HOSPITALS.alpha;
  const context = await newContext();
  await openSidebarByDefault(context, origin);
  const page = await context.newPage();
  await signIn(page, "alpha", user("owner.vikram"));
  const facilityId = await facilityIdOf(page);
  const base = `${origin}/facility/${facilityId}`;

  const dead = watchDeadEnds(page);
  // the default range, as the boxes first show it
  let firstFrom = "";
  let firstTo = "";
  const asked: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === TOTALS_API) asked.push(url.search);
  });

  await test.step("the menu has the totals and no patient search", async () => {
    await page.goto(`${base}/overview`);
    await expect(
      page
        .getByRole("link", { name: "Hospital totals" })
        .filter({ visible: true })
        .first(),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: /^Patients?$/ })).toHaveCount(
      0,
    );
    await expect(
      page.getByRole("link", { name: /Search Patients?/i }),
    ).toHaveCount(0);
    dead.expectNone("on the overview");
  });

  await test.step("this month's totals load", async () => {
    const loaded = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === TOTALS_API &&
        response.request().method() === "GET",
    );
    await page
      .getByRole("link", { name: "Hospital totals" })
      .filter({
        visible: true,
      })
      .first()
      .click();
    expect((await loaded).status()).toBe(200);
    await expect(page.locator('[data-ihr="totals-result"]')).toBeVisible();

    // default range: the first of this month to today, in DD-MM-YYYY
    await expect(page.locator('[data-ihr="totals-from"]')).toHaveValue(
      /^01-\d\d-\d{4}$/,
    );
    await expect(page.locator('[data-ihr="totals-to"]')).toHaveValue(
      /^\d\d-\d\d-\d{4}$/,
    );
    firstFrom = await page.locator('[data-ihr="totals-from"]').inputValue();
    firstTo = await page.locator('[data-ihr="totals-to"]').inputValue();
    // the answer names the range it covers (DD-MM-YYYY)
    await expect(page.locator('[data-ihr="totals-range"]')).toContainText(
      `${firstFrom} to ${firstTo}`,
    );

    // nine tiles; money tiles show rupees, the exact figure is the title
    for (const key of [
      "visits",
      "admissions",
      "discharges",
      "inpatients_now",
      "beds",
      "invoiced",
      "collected",
      "refunded",
      "outstanding_now",
    ]) {
      await expect(page.locator(`[data-ihr="tile-${key}"]`)).toBeVisible();
    }
    for (const key of [
      "invoiced",
      "collected",
      "refunded",
      "outstanding_now",
    ]) {
      const tile = page.locator(`[data-ihr="tile-${key}"]`);
      await expect(tile).toContainText("₹");
      // a lakh or crore word, or an exact figure with two decimals (small amounts)
      await expect(tile).toContainText(/₹[\d,]+\.\d\d( lakh| crore)?/);
      const title = await tile.getAttribute("title");
      expect(title, `the exact figure of ${key}`).toMatch(/^-?₹[\d,]+\.\d\d$/);
    }
    // the help text and the exact-figures table
    await expect(page.locator('[data-ihr="totals-help"]')).toContainText(
      "Outstanding now is today's balance",
    );
    await expect(page.locator('[data-ihr="totals-exact"]')).toBeVisible();
    await expect(
      page.getByText("Exact figures").filter({ visible: true }).first(),
    ).toBeVisible();
    expect(asked, "requests for totals").toHaveLength(1);
    dead.expectNone("while the totals load");
  });

  await test.step("a 400-day range shows its message and sends no request", async () => {
    asked.length = 0;
    await page.locator('[data-ihr="totals-from"]').fill("01-01-2025");
    await page.locator('[data-ihr="totals-to"]').fill("05-02-2026");
    await page.locator('[data-ihr="totals-show"]').click();
    await expect(page.locator('[data-ihr="totals-to-error"]')).toContainText(
      "At most 366 days.",
    );
    // the page waits for a moment, then nothing was asked
    await page.waitForTimeout(1_000);
    expect(asked, "requests for a 400-day range").toHaveLength(0);
    dead.expectNone("after the refused range");
  });

  await test.step("a good range is kept in the address", async () => {
    const loaded = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === TOTALS_API &&
        response.request().method() === "GET",
    );
    await page.locator('[data-ihr="totals-from"]').fill("01-01-2026");
    await page.locator('[data-ihr="totals-to"]').fill("31-01-2026");
    await page.locator('[data-ihr="totals-by"]').selectOption("month");
    await page.locator('[data-ihr="totals-show"]').click();
    expect((await loaded).status()).toBe(200);
    await expect(page).toHaveURL(/from=01-01-2026/);
    await expect(page).toHaveURL(/to=31-01-2026/);
    await expect(page).toHaveURL(/by=month/);
    await expect(
      page.getByText("Month by month").filter({ visible: true }).first(),
    ).toBeVisible();
    await expect(page.locator('[data-ihr="totals-range"]')).toContainText(
      "01-01-2026 to 31-01-2026",
    );
    dead.expectNone("after a good range");
  });

  await test.step("Back: the boxes follow the address the figures follow", async () => {
    await page.goBack();
    await expect(page).not.toHaveURL(/from=01-01-2026/);
    await expect(page.locator('[data-ihr="totals-range"]')).toContainText(
      `${firstFrom} to ${firstTo}`,
    );
    await expect(page.locator('[data-ihr="totals-from"]')).toHaveValue(
      firstFrom,
    );
    await expect(page.locator('[data-ihr="totals-to"]')).toHaveValue(firstTo);
    await expect(page.locator('[data-ihr="totals-by"]')).toHaveValue("day");
    await expect(page.locator('[data-ihr="totals-to-error"]')).toHaveCount(0);
    dead.expectNone("after Back");
  });
});
