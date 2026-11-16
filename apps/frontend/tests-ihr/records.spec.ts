// IndieHR P1-T11c3 (docs/reviews/p1-t11/design-c.md §4 c3, §6, §13.3, §13.4): MRD's records
// access, signed in as `mrd.farah` (alpha).
//
//   a seeded patient's page -> "Ask for records access" -> an insurer request with no reference
//   shows the field error and sends NO request -> reference TEST-1 -> the clinical tabs appear
//   and a clinical read answers 200 -> the records page lists the grant (the watcher's own poll
//   names no patient: review BA1) -> the chart again, reached inside the app -> "End now" on the
//   records page: the list is empty, and Back to the chart shows "not part of your role" with
//   no clinical text and no clinical read (the cache was purged: reviews TS2, TS3) -> a second
//   grant (patient's request) -> the chart -> the grant is ended OUTSIDE the page (the holder's
//   own DELETE through the context's cookies, as another tab would: alpha has no hospital-wide
//   admin login) -> within 35 seconds the page holds none of the chart's text and lands on the
//   records page -> Back is clean the same way.
//
// - The seeded patient is the demo seed's inpatient. MRD cannot read accounts, so the patient is
//   found as `reception.ravi` (lib/desk.ts findSeededStay), in a context of its own.
// - Dead-end listener (lib/deadEnds.ts): no /api/ answer 400, 403, 404 or 5xx, except the 403s
//   CARE's reads (GET /api/v1/) meet after a grant was ended on purpose (declared before the
//   first end); checked at the end of every step (review TS5).
// - What only the chart shows: the clinical history page's "Past Diagnoses" tab and its title
//   "<name>'s clinical history" (src/pages/Patient/History/index.tsx).
// - Every match on text uses .filter({ visible: true }).first(): CARE renders hidden duplicates.
import { watchDeadEnds } from "./lib/deadEnds";
import { facilityIdOf, findSeededStay, openSidebarByDefault } from "./lib/desk";
import { HOSPITALS, expect, signIn, test, user } from "./lib/fixtures";

const RECORDS_API = "/api/ihr/records-access/";
/** The patient page's tab that opens the chart (CARE's "Clinical History") */
const CLINICAL_TAB = /^Clinical History$/;
/** A text only the chart (the clinical history page) shows */
const CHART_TEXT = /^Past Diagnoses$/;
const CLINICAL_READ =
  /\/api\/v1\/patient\/[0-9a-f-]+\/questionnaire_response\//;

test("MRD asks for records access and loses it when it ends (mrd.farah, alpha)", async ({
  newContext,
}) => {
  test.setTimeout(240_000);
  const origin = HOSPITALS.alpha;

  // The patient: found by the front desk (MRD has no account right)
  const deskContext = await newContext();
  const deskPage = await deskContext.newPage();
  await signIn(deskPage, "alpha", user("reception.ravi"));
  const deskFacility = await facilityIdOf(deskPage);
  const stay = await findSeededStay(deskPage, deskFacility, {
    bedVisible: false,
  });

  const context = await newContext();
  await openSidebarByDefault(context, origin);
  const page = await context.newPage();
  await signIn(page, "alpha", user("mrd.farah"));
  const facilityId = await facilityIdOf(page);
  const base = `${origin}/facility/${facilityId}`;

  const dead = watchDeadEnds(page);
  const posts: string[] = [];
  const clinicalReads: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname === RECORDS_API) {
      posts.push(url.pathname);
    }
    if (request.method() === "GET" && CLINICAL_READ.test(url.pathname)) {
      clinicalReads.push(url.pathname);
    }
  });
  // the grant watcher's poll (no ?names=1) and the records page's one list with names
  const polls: Record<string, unknown>[][] = [];
  let namedLists = 0;
  page.on("response", async (response) => {
    const url = new URL(response.url());
    if (
      url.pathname !== RECORDS_API ||
      response.request().method() !== "GET" ||
      response.status() !== 200
    ) {
      return;
    }
    if (url.searchParams.get("names") === "1") {
      namedLists += 1;
      return;
    }
    try {
      const body = (await response.json()) as {
        results: Record<string, unknown>[];
      };
      polls.push(body.results);
    } catch {
      // the page went away before the body was read
    }
  });

  /** The chart's own text is gone, and so is every clinical read, for a short while. */
  async function expectNoChart(where: string) {
    await expect(
      page.getByText(CHART_TEXT).filter({ visible: true }),
      `chart text ${where}`,
    ).toHaveCount(0);
    await expect(
      page
        .getByText(`${stay.patientName}'s clinical history`)
        .filter({ visible: true }),
      `chart title ${where}`,
    ).toHaveCount(0);
    await expect(
      page.getByRole("tab", { name: CLINICAL_TAB }).filter({ visible: true }),
      `clinical tab ${where}`,
    ).toHaveCount(0);
  }

  /** Back from the records page: a positive end state first, then the absence checks. */
  async function expectBackIsClean(where: string) {
    const before = clinicalReads.length;
    await page.goBack();
    // the chart's address, answered by our "not part of your role" page (a fresh patient
    // body without clinical rights: a purged cache)
    await expect(page).toHaveURL(
      new RegExp(`/patient/${stay.patientId}/(clinical_history|history/)`),
    );
    await expect(
      page
        .locator('[data-ihr="not-for-role"]')
        .filter({ visible: true })
        .first(),
    ).toBeVisible();
    await page.waitForTimeout(2_000);
    await expectNoChart(where);
    expect(clinicalReads.slice(before), `clinical reads ${where}`).toEqual([]);
  }

  await test.step("the patient page offers the request, not the chart", async () => {
    await page.goto(`${base}/patient/${stay.patientId}/demography`);
    const tabs = page.getByRole("tablist").getByRole("tab");
    await expect(tabs.first()).toBeVisible();
    await expect(page.getByRole("tab", { name: CLINICAL_TAB })).toHaveCount(0);
    const ask = page
      .locator('[data-ihr="ask-records"] a')
      .filter({ visible: true })
      .first();
    await expect(ask).toBeVisible();
    await expect(ask).toHaveText("Ask for records access");
    await ask.click();
    await expect(page).toHaveURL(/\/ihr\/records\?patient=/);
    await expect(page.locator('[data-ihr="records-page"]')).toBeVisible();
    await expect(page.locator('[data-ihr="records-patient"]')).toContainText(
      stay.patientName,
    );
    // no live grants yet
    await expect(page.locator('[data-ihr="records-live-empty"]')).toBeVisible();
    dead.expectNone("on the patient page and the records page");
  });

  await test.step("an insurer request without a reference: field error, no request", async () => {
    await page.locator('[data-ihr="records-reason"]').selectOption("insurer");
    await page.locator('[data-ihr="records-submit"]').click();
    await expect(page.locator('[data-ihr="error-reference"]')).toBeVisible();
    // the notice names the length the backend gave
    await expect(page.locator('[data-ihr="records-notice"]')).toContainText(
      /for \d+ minutes/,
    );
    expect(posts, "a POST to the records API").toHaveLength(0);
  });

  await test.step("with reference TEST-1 the grant is made and the chart opens", async () => {
    await page.locator('[data-ihr="records-reference"]').fill("TEST-1");
    const created = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === RECORDS_API &&
        response.request().method() === "POST",
    );
    await page.locator('[data-ihr="records-submit"]').click();
    expect((await created).status()).toBe(201);
    await expect(page).toHaveURL(new RegExp(`/patient/${stay.patientId}(/|$)`));
    // the clinical tabs appear, and the banner counts down
    await expect(
      page
        .getByRole("tab", { name: CLINICAL_TAB })
        .filter({ visible: true })
        .first(),
    ).toBeVisible();
    await expect(
      page
        .locator('[data-ihr="grant-banner"]')
        .filter({ visible: true })
        .first(),
    ).toContainText(/\d\d:\d\d left/);
    // one clinical read answers 200: the chart tab's list of answered questionnaires
    const clinical = page.waitForResponse(
      (response) =>
        CLINICAL_READ.test(new URL(response.url()).pathname) &&
        response.request().method() === "GET",
    );
    await page
      .getByRole("tab", { name: CLINICAL_TAB })
      .filter({ visible: true })
      .first()
      .click();
    expect((await clinical).status(), "a clinical read under the grant").toBe(
      200,
    );
    await expect(page).toHaveURL(/\/history\/responses/);
    await expect(
      page.getByText(CHART_TEXT).filter({ visible: true }).first(),
    ).toBeVisible();
    dead.expectNone("while the grant is live");
  });

  await test.step("the records page lists the live grant; the watcher's poll names no one", async () => {
    await page.goto(`${base}/ihr/records`);
    const row = page.locator('[data-ihr="grant-row"]');
    await expect(row).toHaveCount(1);
    await expect(row).toContainText(stay.patientName);
    await expect(row).toContainText("TEST-1");
    await expect(row).toContainText(/\d\d-\d\d-\d{4} \d\d:\d\d IST/);
    await expect(row.locator('[data-ihr="grant-time-left"]')).toContainText(
      /\d\d:\d\d left/,
    );
    // review BA1: names come from the page's own list, never from the watcher's poll
    expect(namedLists, "the records page's list with names").toBeGreaterThan(0);
    expect(polls.length, "the watcher's polls").toBeGreaterThan(0);
    for (const results of polls) {
      for (const grant of results) {
        expect(Object.keys(grant).sort()).toEqual(
          ["expires_at", "id", "patient", "seconds_left"].sort(),
        );
      }
    }
    dead.expectNone("on the records page with a live grant");
  });

  // From here CARE's reads may be refused on purpose (a grant ended): only GET /api/v1/ 403s
  dead.declare({
    method: "GET",
    path: /^\/api\/v1\//,
    status: 403,
    reason: "CARE's reads after a grant was ended on purpose",
  });

  await test.step("End now on the records page clears the chart; Back is clean", async () => {
    // to the chart inside the app (no page load: the cache must be purged by the end itself)
    await page
      .locator('[data-ihr="grant-open"]')
      .filter({ visible: true })
      .first()
      .click();
    await expect(page).toHaveURL(new RegExp(`/patient/${stay.patientId}(/|$)`));
    await expect(
      page
        .locator('[data-ihr="grant-banner"]')
        .filter({ visible: true })
        .first(),
    ).toBeVisible();
    await page
      .getByRole("tab", { name: CLINICAL_TAB })
      .filter({ visible: true })
      .first()
      .click();
    await expect(page).toHaveURL(/\/history\/responses/);
    await expect(
      page.getByText(CHART_TEXT).filter({ visible: true }).first(),
    ).toBeVisible();
    // the records page, still inside the app (raviger follows popstate)
    await page.evaluate((path) => {
      window.history.pushState(null, "", path);
      window.dispatchEvent(new PopStateEvent("popstate"));
    }, `/facility/${facilityId}/ihr/records`);
    await expect(page).toHaveURL(/\/ihr\/records$/);
    const row = page.locator('[data-ihr="grant-row"]');
    await expect(row).toHaveCount(1);
    await row
      .locator('[data-ihr="end-grant"]')
      .filter({ visible: true })
      .first()
      .click();
    const ended = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname.startsWith(RECORDS_API) &&
        response.request().method() === "DELETE",
    );
    await page
      .locator('[data-ihr="end-grant-confirm"]')
      .filter({ visible: true })
      .first()
      .click();
    expect((await ended).status()).toBe(204);
    await expect(page).toHaveURL(/\/ihr\/records/);
    await expect(page.locator('[data-ihr="records-live-empty"]')).toBeVisible();
    await expect(page.locator('[data-ihr="grant-row"]')).toHaveCount(0);
    await expectNoChart("after End now");
    dead.expectNone("after End now");

    await expectBackIsClean("after End now and Back");
    dead.expectNone("after End now and Back");
  });

  await test.step("a second grant; ended outside the page, the chart goes within 35 s", async () => {
    await page.goto(`${base}/ihr/records?patient=${stay.patientId}`);
    await expect(page.locator('[data-ihr="records-live-empty"]')).toBeVisible();
    await page
      .locator('[data-ihr="records-reason"]')
      .selectOption("patient_request");
    const created = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === RECORDS_API &&
        response.request().method() === "POST",
    );
    await page.locator('[data-ihr="records-submit"]').click();
    expect((await created).status()).toBe(201);
    // the patient page (it has the banner), then the chart
    await expect(page).toHaveURL(new RegExp(`/patient/${stay.patientId}(/|$)`));
    await expect(
      page
        .locator('[data-ihr="grant-banner"]')
        .filter({ visible: true })
        .first(),
    ).toBeVisible();
    const clinical = page.waitForResponse(
      (response) =>
        CLINICAL_READ.test(new URL(response.url()).pathname) &&
        response.request().method() === "GET",
    );
    await page
      .getByRole("tab", { name: CLINICAL_TAB })
      .filter({ visible: true })
      .first()
      .click();
    expect((await clinical).status()).toBe(200);
    await expect(page).toHaveURL(/\/history\/responses/);
    await expect(
      page.getByText(CHART_TEXT).filter({ visible: true }).first(),
    ).toBeVisible();

    // Another tab (or an administrator) ends the grant: the holder's own DELETE, with this
    // context's cookies (alpha has no hospital-wide admin login)
    const headers = { "X-IHR-CSRF": "1" };
    const list = await context.request.get(`${origin}${RECORDS_API}`, {
      headers,
    });
    expect(list.status()).toBe(200);
    const grants = (await list.json()) as { results: { id: number }[] };
    expect(grants.results.length, "live grants").toBe(1);
    const ended = await context.request.delete(
      `${origin}${RECORDS_API}${grants.results[0].id}/`,
      { headers },
    );
    expect(ended.status()).toBe(204);

    // The watcher finds it within its 30-second look (or at once, on a refused request)
    await expect(page).toHaveURL(/\/ihr\/records/, { timeout: 40_000 });
    await expect(page.locator('[data-ihr="records-live-empty"]')).toBeVisible();
    await expect(page.locator('[data-ihr="grant-row"]')).toHaveCount(0);
    await expectNoChart("after the grant ended outside the page");
    dead.expectNone("after the grant ended outside the page");
  });

  await test.step("Back does not bring the chart back", async () => {
    await expectBackIsClean("after Back");
    dead.expectNone("after Back");
  });
});
