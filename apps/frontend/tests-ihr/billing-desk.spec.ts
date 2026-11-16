// IndieHR P1-T11c2 (docs/reviews/p1-t11/design-c.md §3.1, §3.3, §4 c2, §9.1; Q-K37a B): the
// billing desks' screens, signed in as `cashier.kavya` and `tpa.joseph` (alpha).
//
//   the inpatient's visit -> status page with the current bed, the bed stays and the account
//   link -> the account -> its tabs (Invoices, Charge Items, Payments, Bed Charge Items; never
//   Reports) -> Bed Charge Items lists the bed by name.
//
// - Only the Cashier adds room rent: "Add Charge Items" is on the Cashier's Bed Charge Items
//   tab and on the TPA Desk's no tab (the backend would refuse it, so the button is not drawn).
// - Dead-end listener (lib/deadEnds.ts) on every step; label absence (design §3.3): no
//   department, care team, ward, location or tag label.
// - The patient is the demo seed's inpatient (ihr_seed_demo: add_inpatient_stay).
import type { Page } from "@playwright/test";

import { watchDeadEnds } from "./lib/deadEnds";
import { facilityIdOf, findSeededStay, openSidebarByDefault } from "./lib/desk";
import { HOSPITALS, expect, signIn, test, user } from "./lib/fixtures";

/** Words of projected fields: none may appear as a label (the bed is allowed to billing). */
const PROJECTED_LABELS = [
  /^blood group$/i,
  /^patient tags$/i,
  /^tags$/i,
  /^department$/i,
  /^care team$/i,
  /^location$/i,
  /^ward$/i,
];

async function expectNoProjectedLabels(page: Page, where: string) {
  for (const label of PROJECTED_LABELS) {
    await expect(
      page.getByText(label),
      `${where}: a "${label.source}" label`,
    ).toHaveCount(0);
  }
}

async function tabNames(page: Page): Promise<string[]> {
  const tabs = page.getByRole("tab");
  await expect(tabs.first()).toBeVisible();
  return (await tabs.allInnerTexts()).map((text) =>
    text.replace(/\s+/g, " ").trim(),
  );
}

// writesBilling: the Cashier writes invoices, payments and charge items; the TPA Desk only reads
// them (and updates the account itself), so a button for those writes would end in a 403.
const DESKS = [
  { username: "cashier.kavya", addsRoomRent: true, writesBilling: true },
  { username: "tpa.joseph", addsRoomRent: false, writesBilling: false },
];

for (const desk of DESKS) {
  test(`the billing desk's screens (${desk.username}, alpha)`, async ({
    newContext,
  }) => {
    const origin = HOSPITALS.alpha;
    const context = await newContext();
    await openSidebarByDefault(context, origin);
    const page = await context.newPage();
    await signIn(page, "alpha", user(desk.username));

    const facilityId = await facilityIdOf(page);
    const stay = await findSeededStay(page, facilityId, { bedVisible: true });
    expect(stay.bedName, "the inpatient's bed").not.toEqual("");
    const base = `${origin}/facility/${facilityId}`;

    const dead = watchDeadEnds(page);

    await test.step("the visit's status page: bed, stays, account link", async () => {
      await page.goto(
        `${base}/patient/${stay.patientId}/encounter/${stay.encounterId}/updates`,
      );
      await expect(page.locator('[data-ihr="encounter-status"]')).toBeVisible();
      await expect(page.locator('[data-ihr="visit-current-bed"]')).toHaveText(
        stay.bedName,
      );
      await expect(
        page.locator('[data-ihr="visit-stay"]').first(),
      ).toContainText(stay.bedName);
      await expect(
        page.locator('[data-ihr="visit-account-link"]'),
      ).toBeVisible();
      await expectNoProjectedLabels(page, "the status page");
      dead.expectNone("on the visit's status page");
    });

    await test.step("the patient page: Demography, Encounters, Accounts (read only)", async () => {
      await page.goto(`${base}/patient/${stay.patientId}/demography`);
      expect(await tabNames(page)).toEqual([
        "Demography",
        "Encounters",
        "Accounts",
      ]);
      // read only: the cashier has no edit right on demographics
      await expect(page.getByRole("button", { name: "Edit" })).toHaveCount(0);
      await expectNoProjectedLabels(page, "Demography");
      dead.expectNone("on the patient page");
    });

    await test.step("the account: its tabs, never Reports", async () => {
      await page.goto(
        `${base}/patient/${stay.patientId}/encounter/${stay.encounterId}/updates`,
      );
      await page.locator('[data-ihr="visit-account-link"]').click();
      await page.waitForURL(/\/billing\/account\/[0-9a-f-]+/);
      const names = await tabNames(page);
      for (const wanted of [
        /^Invoices\b/,
        /^Charge Items\b/,
        /^Payments\b/,
        /^Bed Charge Items\b/,
      ]) {
        expect(
          names.some((name) => wanted.test(name)),
          `a tab ${wanted.source} in ${JSON.stringify(names)}`,
        ).toBe(true);
      }
      expect(names.some((name) => /^Reports\b/.test(name))).toBe(false);
      dead.expectNone("on the account page");
    });

    await test.step("Bed Charge Items lists the bed by name", async () => {
      await page.getByRole("tab", { name: /^Bed Charge Items/ }).click();
      await expect(page).toHaveURL(/\/bed_charge_items(\?|$)/);
      await expect(
        page.getByText(stay.bedName).filter({ visible: true }).first(),
      ).toBeVisible();
      const addRoomRent = page.getByRole("button", {
        name: /Add Charge Items/,
      });
      if (desk.addsRoomRent) {
        await expect(addRoomRent.first()).toBeVisible();
      } else {
        await expect(addRoomRent).toHaveCount(0);
        // nor the general charge items tab's add buttons
        await page.getByRole("tab", { name: /^Charge Items/ }).click();
        await expect(page).toHaveURL(/\/charge_items(\?|$)/);
        await expect(
          page.getByRole("button", { name: /Add Charge Items|Quick Add/ }),
        ).toHaveCount(0);
      }
      await page.waitForLoadState("networkidle");
      dead.expectNone("on Bed Charge Items");
    });

    await test.step("the account's write buttons follow the role's own rights", async () => {
      await page.goto(`${base}/billing/account/${stay.accountId}/charge_items`);
      await expect(
        page.getByText("Amount Due").filter({ visible: true }).first(),
      ).toBeVisible();
      // both desks update the account (can_update_account): tags
      await expect(
        page.getByRole("button", { name: /^(Add|Manage) Tags$/ }),
      ).toBeVisible();
      if (!desk.writesBilling) {
        for (const name of [
          /Create Invoice/,
          /Advance\/Receipt/,
          /Record Credit Note/,
          /Transfer Payment/,
        ]) {
          await expect(
            page.getByRole("button", { name }),
            `a button ${name.source} for ${desk.username}`,
          ).toHaveCount(0);
        }
        // no row menu (edit, change account, mark as): can_update_charge_item is the Cashier's
        await expect(
          page.getByRole("button", { name: "More Actions" }),
        ).toHaveCount(0);
      }
      await page.waitForLoadState("networkidle");
      dead.expectNone("on the account's write buttons");
    });

    await test.step("a typed Reports address moves to a visible tab", async () => {
      await page.goto(`${base}/billing/account/${stay.accountId}/reports`);
      const names = await tabNames(page);
      expect(names.some((name) => /^Reports\b/.test(name))).toBe(false);
      await page.waitForLoadState("networkidle");
      dead.expectNone("when Reports is typed in the address");
    });
  });
}
