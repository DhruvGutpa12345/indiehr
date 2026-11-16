// IndieHR P1-T11c2: small helpers of the front-desk and billing-desk specs. Reads go through the
// page's own session (same origin, our CSRF header), as the web app's calls do.
import type { Page } from "@playwright/test";

import { expect } from "./fixtures";

/** GET a JSON answer from the page's origin. Fails the test on any non-200. */
export async function apiJson<T>(page: Page, path: string): Promise<T> {
  const result = await page.evaluate(async (url) => {
    const res = await fetch(url, {
      headers: { "X-IHR-CSRF": "1" },
      credentials: "same-origin",
    });
    return { status: res.status, body: res.ok ? await res.json() : null };
  }, path);
  expect(result.status, `GET ${path}`).toBe(200);
  return result.body as T;
}

/** The facility the signed-in user belongs to (every seeded login has exactly one). */
export async function facilityIdOf(page: Page): Promise<string> {
  const user = await apiJson<{ facilities?: { id: string }[] }>(
    page,
    "/api/v1/users/getcurrentuser/",
  );
  const id = user.facilities?.[0]?.id ?? "";
  expect(id, "the user's facility").not.toEqual("");
  return id;
}

/** The sidebar opens expanded (CARE keeps this in a cookie), so links are in the page. */
export async function openSidebarByDefault(
  context: import("@playwright/test").BrowserContext,
  origin: string,
) {
  await context.addCookies([
    { name: "sidebar:state", value: "true", url: origin },
  ]);
}

export interface SeededStay {
  accountId: string;
  patientId: string;
  patientName: string;
  phone: string;
  yearOfBirth: number | null;
  encounterId: string;
  bedName: string;
}

interface AccountRow {
  id: string;
}
interface AccountDetail {
  id: string;
  patient: {
    id: string;
    name: string;
    phone_number: string;
    year_of_birth: number | null;
  };
  primary_encounter: {
    id: string;
    encounter_class: string;
    current_location: { name: string } | null;
    location_history: { location: { name: string } }[];
  } | null;
}

/**
 * The demo seed's inpatient (ihr_seed_demo's add_inpatient_stay): the account whose primary
 * visit is an inpatient stay (with a bed, when `bedVisible`: billing roles see it, the front desk
 * does not). Read as the signed-in user.
 */
export async function findSeededStay(
  page: Page,
  facilityId: string,
  { bedVisible }: { bedVisible: boolean },
): Promise<SeededStay> {
  const list = await apiJson<{ results: AccountRow[] }>(
    page,
    `/api/v1/facility/${facilityId}/account/?limit=50`,
  );
  for (const row of list.results) {
    const account = await apiJson<AccountDetail>(
      page,
      `/api/v1/facility/${facilityId}/account/${row.id}/`,
    );
    const visit = account.primary_encounter;
    // the front desk does not see the bed (projected away): the inpatient class is enough there
    if (
      visit &&
      visit.encounter_class === "imp" &&
      (!bedVisible || visit.current_location)
    ) {
      return {
        accountId: account.id,
        patientId: account.patient.id,
        patientName: account.patient.name,
        phone: account.patient.phone_number,
        yearOfBirth: account.patient.year_of_birth,
        encounterId: visit.id,
        bedName: visit.current_location?.name ?? "",
      };
    }
  }
  throw new Error(
    "No account with an inpatient visit in a bed: run scripts/care-dev.sh seed (add_inpatient_stay)",
  );
}
