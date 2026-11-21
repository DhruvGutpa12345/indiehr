// IndieHR P1-T24a (docs/reviews/p1-t24/design.md §2.1, attack.md "Decision"): the audit trail page,
// signed in as `admin.priya` (alpha), the seeded hospital-wide admin (Q-K117 A).
//
//   the menu has "Audit trail" -> the page asks for the list and the status, and shows today
//   (IST) in DD-MM-YYYY -> the status strip is there -> opening an entry asks for that one
//   entry (its values come only now) -> the CSV, JSON and verifier downloads are same-origin
//   links that carry today's day (and a range over 31 days has none) -> an entry's actor is a link that filters on that exact
//   actor -> a mistaken date shows its message and sends NO request -> a kind filter is sent.
//
// - Dead-end listener (lib/deadEnds.ts): no /api/ answer 400, 403, 404 or 5xx.
// - Matches on text use .filter({ visible: true }).first(); data-ihr attributes are ours.
// - Waits are for the API answer (waitForResponse), never network idle.
// - Who may NOT see the page: roles-menus.spec.ts (menus and typed addresses of every login).
import { watchDeadEnds } from "./lib/deadEnds";
import { facilityIdOf, openSidebarByDefault } from "./lib/desk";
import { HOSPITALS, expect, signIn, test, user } from "./lib/fixtures";

const ENTRIES_API = "/api/ihr/audit/entries/";
const STATUS_API = "/api/ihr/audit/status/";

function isList(response: { url(): string; request(): { method(): string } }) {
  return (
    new URL(response.url()).pathname === ENTRIES_API &&
    response.request().method() === "GET"
  );
}

test("the audit trail (admin.priya, alpha)", async ({ newContext }) => {
  const origin = HOSPITALS.alpha;
  const context = await newContext();
  await openSidebarByDefault(context, origin);
  const page = await context.newPage();
  await signIn(page, "alpha", user("admin.priya"));
  const facilityId = await facilityIdOf(page);
  const base = `${origin}/facility/${facilityId}`;

  const dead = watchDeadEnds(page);
  const listQueries: string[] = [];
  const entryCalls: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === ENTRIES_API) listQueries.push(url.search);
    else if (url.pathname.startsWith(ENTRIES_API))
      entryCalls.push(url.pathname);
  });

  await test.step("the menu has the audit trail; the page loads the list and the status", async () => {
    await page.goto(`${base}/overview`);
    const link = page
      .getByRole("link", { name: "Audit trail" })
      .filter({ visible: true })
      .first();
    await expect(link).toBeVisible();
    const listed = page.waitForResponse(isList);
    const status = page.waitForResponse(
      (response) => new URL(response.url()).pathname === STATUS_API,
    );
    await link.click();
    expect((await listed).status()).toBe(200);
    expect((await status).status()).toBe(200);
    await expect(page.locator('[data-ihr="audit-page"]')).toBeVisible();
    await expect(page.locator('[data-ihr="audit-intro"]')).toContainText(
      "Opening an entry is recorded too.",
    );
    // before any entry is opened, no entry (values) was asked for
    expect(entryCalls, "entry calls before opening one").toEqual([]);
    dead.expectNone("on the audit page");
  });

  await test.step("today in IST, DD-MM-YYYY, and the status strip", async () => {
    const from = page.locator('[data-ihr="audit-from"]');
    const to = page.locator('[data-ihr="audit-to"]');
    await expect(from).toHaveValue(/^\d\d-\d\d-\d{4}$/);
    await expect(to).toHaveValue(await from.inputValue());
    // the first request carried the same day, as YYYY-MM-DD
    const [day, month, year] = (await from.inputValue()).split("-");
    expect(listQueries[0]).toContain(`from=${year}-${month}-${day}`);
    expect(listQueries[0]).toContain(`to=${year}-${month}-${day}`);

    const strip = page.locator('[data-ihr="audit-status"]');
    await expect(strip).toBeVisible();
    await expect(
      page.locator('[data-ihr="audit-status-sealed"]'),
    ).toContainText(
      /Last sealed \d\d-\d\d-\d{4} \d\d:\d\d:\d\d IST|Nothing sealed yet/,
    );
    await expect(
      page.locator('[data-ihr="audit-status-unsealed"]'),
    ).toContainText(/Not yet sealed: \d+/);
    await expect(
      page.locator('[data-ihr="audit-status-triggers"]'),
    ).toContainText("Triggers: all in place");
    await expect(
      page.locator('[data-ihr="audit-status-fingerprints"]'),
    ).toContainText("Outside fingerprints:");
  });

  await test.step("the downloads are links for today's day; the CSV is a file (P1-T24c)", async () => {
    const csv = page.locator('[data-ihr="audit-export-csv"]');
    const json = page.locator('[data-ihr="audit-export-json"]');
    await expect(csv).toBeVisible();
    const [day, month, year] = (
      await page.locator('[data-ihr="audit-from"]').inputValue()
    ).split("-");
    const days = `from=${year}-${month}-${day}&to=${year}-${month}-${day}`;
    await expect(csv).toHaveAttribute(
      "href",
      new RegExp(`^/api/ihr/audit/export/\\?${days}&file=csv$`),
    );
    await expect(json).toHaveAttribute("href", new RegExp(`&file=json$`));
    await expect(page.locator('[data-ihr="audit-verifier"]')).toHaveAttribute(
      "href",
      "/api/ihr/audit/verifier.py",
    );
    const download = page.waitForEvent("download");
    await csv.click();
    expect((await download).suggestedFilename()).toMatch(
      /^audit-[\w-]+-\d{4}-\d\d-\d\d-to-\d{4}-\d\d-\d\d\.csv$/,
    );
    dead.expectNone("downloading the CSV");
  });

  await test.step("the list shows entries by this sign-in's own user; times are IST", async () => {
    const rows = page
      .locator('[data-ihr="audit-row"]')
      .filter({ visible: true });
    await expect(rows.first()).toBeVisible();
    // admin.priya signs in as the CARE user care-admin (seeding.HOSPITAL_ADMIN), and her own
    // requests to the audit routes are in today's list
    await expect(
      page
        .locator('[data-ihr="audit-actor"]')
        .filter({ visible: true, hasText: "care-admin" })
        .first(),
    ).toBeVisible();
    await expect(
      page.locator('[data-ihr="audit-open"]').filter({ visible: true }).first(),
    ).toContainText(/^\d\d-\d\d-\d{4} \d\d:\d\d:\d\d IST$/);
  });

  await test.step("opening an entry asks for that one entry and shows its note", async () => {
    const open = page
      .locator('[data-ihr="audit-open"]')
      .filter({ visible: true })
      .first();
    const detail = page.waitForResponse((response) =>
      /^\/api\/ihr\/audit\/entries\/\d+\/$/.test(
        new URL(response.url()).pathname,
      ),
    );
    await open.click();
    expect((await detail).status()).toBe(200);
    const sheet = page.locator('[data-ihr="audit-sheet"]');
    await expect(sheet).toBeVisible();
    await expect(sheet).toContainText("Opening an entry is recorded");
    await expect(page.locator('[data-ihr="audit-facts"]')).toBeVisible();
    // values (or the line saying there are none) are shown once the entry has loaded
    await expect(
      page
        .locator('[data-ihr="audit-values"], [data-ihr="audit-values-none"]')
        .first(),
    ).toBeVisible();
    expect(entryCalls).toHaveLength(1);
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);
    dead.expectNone("opening an entry");
  });

  await test.step("an entry's actor filters on that exact actor", async () => {
    const actor = page
      .locator('[data-ihr="audit-actor"]')
      .filter({ visible: true })
      .first();
    const name = (await actor.innerText()).trim();
    const filtered = page.waitForResponse(
      (response) => isList(response) && response.url().includes("actor_exact="),
    );
    await actor.click();
    expect((await filtered).status()).toBe(200);
    await expect(page.locator('[data-ihr="audit-chip-actor"]')).toContainText(
      name,
    );
    expect(page.url()).toContain("actor=");
    // every row shown is by that person
    const shown = page
      .locator('[data-ihr="audit-actor"]')
      .filter({ visible: true });
    await expect(shown.first()).toHaveText(name);
    for (const text of await shown.allInnerTexts()) {
      expect(text.trim()).toBe(name);
    }
    // and "Show all" clears it. No wait for a list request here (review W1): "Show all"
    // brings back the first load's filters, whose list is still fresh in the page's cache
    // (staleTime 60 s), so normally no request is sent. Check what the page shows instead.
    await page
      .locator('[data-ihr="audit-chip-actor"]')
      .getByRole("button", { name: "Show all" })
      .click();
    await expect(page.locator('[data-ihr="audit-chip-actor"]')).toHaveCount(0);
    await expect.poll(() => page.url()).not.toContain("actor=");
    await expect(
      page.locator('[data-ihr="audit-row"]').filter({ visible: true }).first(),
    ).toBeVisible();
    dead.expectNone("filtering by actor");
  });

  await test.step("the kind filter is sent to the API", async () => {
    const filtered = page.waitForResponse(
      (response) => isList(response) && response.url().includes("kind=request"),
    );
    await page.locator('[data-ihr="audit-kind"]').selectOption("request");
    expect((await filtered).status()).toBe(200);
    dead.expectNone("filtering by kind");
  });

  await test.step("a kind chosen after a date was typed keeps the date (review W2)", async () => {
    const fromBox = page.locator('[data-ihr="audit-from"]');
    await fromBox.fill("");
    await fromBox.pressSequentially("01112026");
    const filtered = page.waitForResponse(
      (response) =>
        isList(response) &&
        response.url().includes("kind=write") &&
        response.url().includes("from=2026-11-01"),
    );
    await page.locator('[data-ihr="audit-kind"]').selectOption("write");
    expect((await filtered).status()).toBe(200);
    await expect(fromBox).toHaveValue("01-11-2026");
    dead.expectNone("a kind chosen after a typed date");
  });

  await test.step("a range over 31 days has no download link, only its message (P1-T24c)", async () => {
    const fromBox = page.locator('[data-ihr="audit-from"]');
    await fromBox.fill("");
    await fromBox.pressSequentially("01012026");
    await page.locator('[data-ihr="audit-show"]').click();
    await expect(
      page.locator('[data-ihr="audit-export-problem"]'),
    ).toContainText("at most 31 days");
    await expect(page.locator('[data-ihr="audit-export-csv"]')).toHaveCount(0);
    await expect(page.locator('[data-ihr="audit-export-json"]')).toHaveCount(0);
    dead.expectNone("a range over 31 days");
  });

  await test.step("a mistaken date shows its message and sends no request", async () => {
    const before = listQueries.length;
    await page.locator('[data-ihr="audit-from"]').fill("");
    await page.locator('[data-ihr="audit-from"]').pressSequentially("32132026");
    await page.locator('[data-ihr="audit-show"]').click();
    await expect(page.locator('[data-ihr="audit-from-error"]')).toBeVisible();
    // positive wait first, then the negative check
    expect(listQueries.length, "list requests after a mistaken date").toBe(
      before,
    );
    dead.expectNone("a mistaken date");
  });
});
