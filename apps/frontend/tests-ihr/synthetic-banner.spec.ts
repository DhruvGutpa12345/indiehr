// IndieHR P1-T32 E5 (ADR-020 §3, docs/reviews/p1-t32/design.md §7 and §8.3): the "synthetic
// data only" bar. The dev stack and CI are not production, so every page shows it (public
// bootstrap.js, SYNTHETIC_SCRIPT), in English and Hindi, and it never covers a control:
//
//   1. the sign-in page: the bar is visible with both texts, and the page's first control starts
//      below it;
//   2. a signed-in patient list page: the bar is there and the side bar starts below it;
//   3. a computer on another time zone (this test only): the time-zone bar and the synthetic bar
//      both show, stacked, the time-zone bar offset by the published `--ihr-synthetic-h`.
//
// Every other spec in this folder also runs with the bar on, so a layout it breaks fails those
// specs too (nothing else is needed for "the whole suite passes with the banner on").
//
// Needs the real stack (CI job "Browser isolation (Playwright)"): the web app is never built on
// the development machine, so this spec runs in CI only.
import type { Page } from "@playwright/test";

import { facilityIdOf, openSidebarByDefault } from "./lib/desk";
import {
  HOSPITALS,
  SIGN_IN_BUTTON,
  expect,
  signIn,
  test,
  user,
} from "./lib/fixtures";

const BANNER = "#ihr-synthetic";
const IST_BAR = "#ihr-tz-warning";
const TEXT_EN = "Synthetic data only: do not enter real patients.";
const TEXT_HI =
  "केवल कृत्रिम (सिंथेटिक) डेटा: असली मरीज़ों की जानकारी दर्ज न करें।";

async function box(page: Page, selector: string) {
  const found = await page.locator(selector).first().boundingBox();
  expect(found, `${selector} has a box`).not.toBeNull();
  return found!;
}

/** The bar's two lines, its role and the height it publishes for the time-zone bar. */
async function expectBanner(page: Page) {
  const banner = page.locator(BANNER);
  await expect(banner).toBeVisible();
  await expect(banner).toHaveAttribute("role", "note");
  await expect(banner.locator('[lang="en"]')).toHaveText(TEXT_EN);
  await expect(banner.locator('[lang="hi"]')).toHaveText(TEXT_HI);
  const published = await page.evaluate(() =>
    document.documentElement.style.getPropertyValue("--ihr-synthetic-h"),
  );
  const height = (await box(page, BANNER)).height;
  expect(Number.parseFloat(published), "--ihr-synthetic-h").toBeGreaterThan(0);
  expect(Math.abs(Number.parseFloat(published) - height)).toBeLessThan(2);
}

test("sign-in page: the bar shows in both languages and covers no control", async ({
  newContext,
}) => {
  const context = await newContext();
  const page = await context.newPage();
  await page.goto(`${HOSPITALS.alpha}/login`);
  const button = page.getByRole("button", { name: SIGN_IN_BUTTON });
  await expect(button).toBeVisible();
  await expectBanner(page);

  const banner = await box(page, BANNER);
  expect(banner.y, "the bar is at the very top").toBeLessThanOrEqual(1);
  const control = (await button.boundingBox())!;
  expect(
    control.y,
    "the first control starts below the bar",
  ).toBeGreaterThanOrEqual(banner.y + banner.height - 1);
});

test("signed in: the bar is on a patient list page and the side bar starts below it", async ({
  newContext,
}) => {
  const context = await newContext();
  await openSidebarByDefault(context, HOSPITALS.alpha);
  const page = await context.newPage();
  await signIn(page, "alpha", user("reception.ravi"));
  const facilityId = await facilityIdOf(page);
  await page.goto(
    `${HOSPITALS.alpha}/facility/${facilityId}/encounters/patients/all`,
  );
  await expectBanner(page);

  const banner = await box(page, BANNER);
  // The app's full-height fixed panel (Tailwind `fixed inset-y-0`: the side bar)
  const panel = page.locator(".fixed.inset-y-0").filter({ visible: true });
  await expect(panel.first()).toBeVisible();
  const top = (await panel.first().boundingBox())!.y;
  expect(top, "the side bar's top is below the bar").toBeGreaterThanOrEqual(
    banner.y + banner.height - 1,
  );
});

test("another time zone: the time-zone bar and the synthetic bar stack without overlap", async ({
  newContext,
}) => {
  const context = await newContext({ timezoneId: "UTC" });
  const page = await context.newPage();
  await page.goto(`${HOSPITALS.alpha}/login`);
  await expectBanner(page);
  await expect(page.locator(IST_BAR)).toBeVisible();

  const synthetic = await box(page, BANNER);
  const ist = await box(page, IST_BAR);
  expect(
    ist.y,
    "the time-zone bar starts at the synthetic bar's bottom edge",
  ).toBeGreaterThanOrEqual(synthetic.y + synthetic.height - 1);
  expect(synthetic.y + synthetic.height).toBeLessThanOrEqual(ist.y + 1);

  const control = (await page
    .getByRole("button", { name: SIGN_IN_BUTTON })
    .boundingBox())!;
  expect(
    control.y,
    "the first control starts below both bars",
  ).toBeGreaterThanOrEqual(ist.y + ist.height - 1);
});
