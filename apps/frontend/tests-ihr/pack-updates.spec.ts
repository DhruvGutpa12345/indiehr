// IndieHR P1-T24c (docs/reviews/p1-t24/design.md "T24c as decided"): the reference-list updates
// page, signed in as `admin.priya` (alpha), the seeded hospital-wide admin (Q-K117 A).
//
//   the menu has "Reference list updates" -> the list asks for the waiting items and shows the
//   seeded one (G5: `scripts/care-dev.sh seed` leaves exactly one waiting) -> opening it asks for
//   that one item and shows what accepting does -> Keep ours without a reason sends NOTHING ->
//   with a reason it is a POST that answers 200 -> the item leaves "Waiting", is under "Kept as
//   ours", and History shows the decision with the reason. The last step accepts the item back
//   from "Kept as ours" (a real Accept POST, 200), which restores the seed's starting state, so the spec can run
//   twice against one stack without a re-seed (the seed adds the waiting item again if none is).
//
// - Dead-end listener (lib/deadEnds.ts): no /api/ answer 400, 403, 404 or 5xx. The one refusal
//   this spec could cause (a missing reason) is stopped by the page before any request.
// - The list is read afresh on every visit (staleTime 0) and the page never refetches on focus,
//   so a waitForResponse registered before the click always sees a request.
// - Waits are for the API answer (waitForResponse), never network idle.
// - Who may NOT see the page: roles-menus.spec.ts (menus and typed addresses of every login).
import { watchDeadEnds } from "./lib/deadEnds";
import { facilityIdOf, openSidebarByDefault } from "./lib/desk";
import { HOSPITALS, expect, signIn, test, user } from "./lib/fixtures";

const LIST_API = "/api/ihr/packs/updates/";
const HISTORY_API = "/api/ihr/packs/history/";
// deploy/locale/en.json ihr_packs_done_accept
const ACCEPTED_NOTICE = "Accepted: the pack's version is now in place.";

function isList(state: string) {
  return (response: { url(): string; request(): { method(): string } }) => {
    const url = new URL(response.url());
    return (
      url.pathname === LIST_API &&
      url.searchParams.get("state") === state &&
      response.request().method() === "GET"
    );
  };
}

test("the reference list updates (admin.priya, alpha)", async ({
  newContext,
}) => {
  const origin = HOSPITALS.alpha;
  const context = await newContext();
  await openSidebarByDefault(context, origin);
  const page = await context.newPage();
  await signIn(page, "alpha", user("admin.priya"));
  const facilityId = await facilityIdOf(page);
  const base = `${origin}/facility/${facilityId}`;

  const dead = watchDeadEnds(page);
  const posts: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith(LIST_API) && request.method() === "POST") {
      posts.push(url.pathname);
    }
  });

  let label = "";
  const reason = "ticket P1-T24c playwright";

  await test.step("the menu has the page; it lists the waiting items", async () => {
    await page.goto(`${base}/overview`);
    const link = page
      .getByRole("link", { name: "Reference list updates" })
      .filter({ visible: true })
      .first();
    await expect(link).toBeVisible();
    const listed = page.waitForResponse(isList("pending"));
    await link.click();
    expect((await listed).status()).toBe(200);
    await expect(page.locator('[data-ihr="packs-page"]')).toBeVisible();
    await expect(page.locator('[data-ihr="packs-intro"]')).toContainText(
      "Every decision is recorded in the audit trail.",
    );
    // the seed leaves one item waiting (run `scripts/care-dev.sh seed` if this is empty)
    const open = page
      .locator('[data-ihr="packs-open"]')
      .filter({ visible: true })
      .first();
    await expect(
      open,
      "a waiting pack item (scripts/care-dev.sh seed makes one)",
    ).toBeVisible();
    label = (await open.innerText()).trim();
    dead.expectNone("on the pack updates page");
  });

  await test.step("opening an item asks for that item and says what accepting does", async () => {
    const detail = page.waitForResponse((response) =>
      /^\/api\/ihr\/packs\/updates\/\d+\/$/.test(
        new URL(response.url()).pathname,
      ),
    );
    await page
      .locator('[data-ihr="packs-open"]')
      .filter({ visible: true })
      .first()
      .click();
    expect((await detail).status()).toBe(200);
    const sheet = page.locator('[data-ihr="packs-sheet"]');
    await expect(sheet).toBeVisible();
    await expect(page.locator('[data-ihr="packs-effect"]')).toContainText(
      "What accepting does:",
    );
    await expect(page.locator('[data-ihr="packs-fields"]')).toBeVisible();
    // a waiting item offers both decisions
    await expect(page.locator('[data-ihr="packs-keep"]')).toBeVisible();
    await expect(page.locator('[data-ihr="packs-accept"]')).toBeVisible();
    dead.expectNone("opening an item");
  });

  await test.step("Keep ours without a reason sends nothing", async () => {
    await page.locator('[data-ihr="packs-keep"]').click();
    await expect(page.locator('[data-ihr="packs-reason-error"]')).toBeVisible();
    expect(posts, "decision requests without a reason").toEqual([]);
    dead.expectNone("a missing reason");
  });

  await test.step("Keep ours with a reason is saved, and the page says so", async () => {
    await page.locator('[data-ihr="packs-reason"]').fill(reason);
    const decided = page.waitForResponse(
      (response) =>
        /^\/api\/ihr\/packs\/updates\/\d+\/$/.test(
          new URL(response.url()).pathname,
        ) && response.request().method() === "POST",
    );
    await page.locator('[data-ihr="packs-keep"]').click();
    expect((await decided).status()).toBe(200);
    await expect(page.locator('[data-ihr="packs-sheet"]')).toHaveCount(0);
    await expect(page.locator('[data-ihr="packs-notice"]')).toContainText(
      "Kept: our version stays",
    );
    expect(posts).toHaveLength(1);
    dead.expectNone("keeping ours");
  });

  await test.step("the item is under Kept as ours, which offers Accept but not Keep", async () => {
    const kept = page.waitForResponse(isList("kept"));
    await page.locator('[data-ihr="packs-tab-kept"]').click();
    expect((await kept).status()).toBe(200);
    const row = page
      .locator('[data-ihr="packs-open"]')
      .filter({ visible: true, hasText: label })
      .first();
    await expect(row).toBeVisible();
    await row.click();
    await expect(page.locator('[data-ihr="packs-accept"]')).toBeVisible();
    // "Keep ours" is for waiting items only
    await expect(page.locator('[data-ihr="packs-keep"]')).toHaveCount(0);
    await expect(page.locator('[data-ihr="packs-kept"]')).toBeVisible();
    dead.expectNone("the kept tab");
  });

  await test.step("Accept from the kept tab (with a reason) is saved: the seed's starting state is back", async () => {
    // the sheet opened above is still open; its reason box is new and empty
    await page.locator('[data-ihr="packs-reason"]').fill(`${reason} accept`);
    const decided = page.waitForResponse(
      (response) =>
        /^\/api\/ihr\/packs\/updates\/\d+\/$/.test(
          new URL(response.url()).pathname,
        ) && response.request().method() === "POST",
    );
    await page.locator('[data-ihr="packs-accept"]').click();
    expect((await decided).status()).toBe(200);
    await expect(page.locator('[data-ihr="packs-sheet"]')).toHaveCount(0);
    await expect(page.locator('[data-ihr="packs-notice"]')).toContainText(
      ACCEPTED_NOTICE,
    );
    expect(posts).toHaveLength(2);
    dead.expectNone("accepting");
  });

  await test.step("History shows the decision with the reason", async () => {
    const history = page.waitForResponse(
      (response) => new URL(response.url()).pathname === HISTORY_API,
    );
    await page.locator('[data-ihr="packs-tab-history"]').click();
    expect((await history).status()).toBe(200);
    const row = page
      .locator('[data-ihr="packs-history-row"][data-ihr-outcome="kept"]')
      .filter({ visible: true, hasText: reason })
      .first();
    await expect(row).toBeVisible();
    await expect(row).toContainText("Kept ours");
    await expect(row.locator('[data-ihr="packs-history-item"]')).toBeVisible();
    // days and times are India time
    await expect(row).toContainText(/\d\d-\d\d-\d{4} \d\d:\d\d IST/);
    dead.expectNone("the history tab");
  });
});
