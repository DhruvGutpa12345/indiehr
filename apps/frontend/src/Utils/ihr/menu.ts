// IndieHR P1-T11c1 (docs/reviews/p1-t11/design-c.md §2.2, §3.2, §13.2, §13.5): the menu rules.
// Pure (no React, no "@/" imports). `facility-nav.tsx` and `location-nav.tsx` pass CARE's own
// link lists through these functions (src/components/ihr/ihrMenu.tsx adds names and icons).
//
// - pending (my-access not answered yet, or failed with nothing cached): only the items every
//   mode keeps (Overview, Users) as CARE has them; every other facility item and every location
//   item hidden, none of ours (design §13.8: fails closed, no flash of CARE's full menu).
// - care: CARE's links, unchanged, plus our items the user holds (Records access, Hospital totals,
//   and for a hospital-wide admin the Audit trail and the Reference list updates).
// - restricted: CARE's links with `visibility` set by URL suffix (tables below), parents with no
//   visible child hidden, a parent's url pointing at its first visible child, plus our items.
// A URL suffix not in the tables is left as CARE has it (fails toward CARE: a dead end at worst,
// never a leak; the backend refuses). scripts/check-ihr-frontend.py fails when upstream renames
// a suffix named here.
import type { IhrAccess } from "./access";

export interface LinkLike {
  name: string;
  url: string;
  visibility?: boolean;
  children?: LinkLike[];
}

export type OurItemKey = "records" | "totals" | "places" | "audit" | "packs";

export interface OurItem {
  key: OurItemKey;
  url: string;
}

export const OUR_ITEM_PATHS: Record<OurItemKey, string> = {
  records: "/ihr/records",
  totals: "/ihr/totals",
  places: "/ihr/places",
  audit: "/ihr/audit",
  packs: "/ihr/packs",
};

/** Our menu items for this user, in menu order (always after CARE's own links). */
export function ihrOurItems(access: IhrAccess, baseUrl: string): OurItem[] {
  if (access.mode === "pending") return [];
  const keys: OurItemKey[] = [];
  if (access.records) keys.push("records");
  if (access.totals) keys.push("totals");
  // the audit trail: the backend's `hospital_admin` (care mode only; restricted roles hold no
  // admin role and a superuser never has the flag), shown last (P1-T24a, Q-K110 A, Q-K111 A)
  // and the reference-list updates, after it (P1-T24c): the same gate, so never a restricted role
  if (access.hospitalAdmin) keys.push("audit", "packs");
  if (access.mode === "restricted" && placeSlug(access) !== undefined) {
    keys.push("places");
  }
  return keys.map((key) => ({ key, url: `${baseUrl}${OUR_ITEM_PATHS[key]}` }));
}

/** The services shortcut on the overview page follows the `/services` row (care mode only). */
export function ihrShowsServices(access: IhrAccess): boolean {
  return access.mode === "care";
}

type Visible<T extends LinkLike> = T & { visibility?: boolean };

function suffixOf(url: string, baseUrl: string): string {
  return url.startsWith(baseUrl) ? url.slice(baseUrl.length) : url;
}

function setVisible<T extends LinkLike>(link: T, allowed: boolean): T {
  const visibility = link.visibility !== false && allowed;
  return { ...link, visibility } as Visible<T>;
}

function isShown(link: LinkLike): boolean {
  return link.visibility !== false;
}

/** A parent whose children were all hidden is hidden; its url is its first visible child's. */
function withChildren<T extends LinkLike>(
  link: T,
  children: LinkLike[],
  parentAllowed = true,
): T {
  const firstShown = children.find(isShown);
  const visibility =
    link.visibility !== false && parentAllowed && firstShown !== undefined;
  return {
    ...link,
    url: firstShown ? firstShown.url : link.url,
    visibility,
    children,
  } as unknown as T;
}

function mapChildren<T extends LinkLike>(
  link: T,
  rule: (child: LinkLike) => boolean | undefined,
): LinkLike[] {
  return (link.children ?? []).map((child) => {
    const allowed = rule(child);
    return allowed === undefined ? child : setVisible(child, allowed);
  });
}

// ---------------------------------------------------------------------------------------------
// Facility menu (facility-nav.tsx), restricted mode (design §3.2)

/** The items every mode keeps as CARE has them: the only ones shown while pending. */
const PENDING_KEEP: ReadonlySet<string> = new Set(["/overview", "/users"]);

type FacilityTopRule = "keep" | "never" | "patients" | "encounters" | "billing";

// CARE 3.1 (P2-T0b) made the Patients parent flat: Search patients, Encounters (one item whose
// element opens the class lists, url ending without a class) and Search by location are now
// top-level items, Referrals is new, and Settings has no children here (its 15 pages and the
// templates moved to the settings sidebar, facility-settings-nav.tsx, which has no menu rule:
// the settings routes are reached by address only and the backend refuses a restricted role).
function facilityTopRule(
  suffix: string,
  hasChildren: boolean,
): FacilityTopRule {
  if (suffix === "/overview") return "keep";
  if (suffix === "/appointments" || suffix === "/queues") return "keep"; // CARE's own rule
  if (suffix === "/patients") return "patients";
  if (suffix === "/encounters/patients") return "encounters";
  if (suffix === "/encounters/locations") return "never"; // the bed board shows occupants (Q-K37)
  // services, resource and referrals: clinical or operational lists (referral routes are closed
  // to restricted users in the backend, P2-T0a, Q-K163); never offered to restricted roles
  if (suffix === "/services" || suffix === "/resource") return "never";
  if (suffix === "/referrals") return "never";
  if (suffix === "/users") return "keep";
  if (suffix === "/billing" && hasChildren) return "billing";
  // The Settings item (/settings/general, plus any /settings/ item upstream adds back) is
  // configuration
  if (suffix.startsWith("/settings/")) return "never";
  return "keep"; // plugin links and anything new: as CARE has it
}

/** One leaf item set to this rule's answer, whatever CARE's own permission check said. */
function forceVisible<T extends LinkLike>(link: T, allowed: boolean): T {
  // CARE's own rule (booking or encounter rights) would hide the Cashier's and MRD's entry:
  // it shows when our rule allows it (the same override the old Patients parent had)
  return { ...link, visibility: allowed } as Visible<T>;
}

function billingChildRule(
  suffix: string,
  access: IhrAccess,
): boolean | undefined {
  if (suffix === "/billing/account") return access.has("can_read_account");
  if (suffix === "/billing/invoices") return access.has("can_read_invoice");
  if (suffix === "/billing/payments") {
    return access.has("can_read_payment_reconciliation");
  }
  return undefined;
}

/**
 * CARE's facility links, as this user may use them, then our items.
 * `makeOurs` builds one link of ours (name and icon) from an item.
 */
export function ihrFacilityLinks<T extends LinkLike>(
  links: T[],
  access: IhrAccess,
  baseUrl: string,
  makeOurs: (item: OurItem) => T,
): T[] {
  if (access.mode === "pending") {
    return links.map((link) =>
      PENDING_KEEP.has(suffixOf(link.url, baseUrl))
        ? link
        : setVisible(link, false),
    );
  }
  const ours = ihrOurItems(access, baseUrl).map(makeOurs);
  if (access.mode === "care") return ours.length ? [...links, ...ours] : links;

  const shaped = links.map((link) => {
    const suffix = suffixOf(link.url, baseUrl);
    switch (facilityTopRule(suffix, !!link.children)) {
      case "never":
        return setVisible(link, false);
      case "patients":
        return forceVisible(link, access.demographics);
      case "encounters":
        return forceVisible(link, access.encounterStatus);
      case "billing": {
        const children = mapChildren(link, (child) =>
          billingChildRule(suffixOf(child.url, baseUrl), access),
        );
        return withChildren(link, children);
      }
      default:
        return link;
    }
  });
  return [...shaped, ...ours];
}

// ---------------------------------------------------------------------------------------------
// Location menu (location-nav.tsx), restricted mode (design §3.2, §13.5)

/** Pages "My places" may open, in order: the choice for a link and for the menu is one rule. */
const PLACE_PAGES = ["/service_requests", "/inventory/summary"] as const;

function locationLeafRule(
  suffix: string,
  access: IhrAccess,
): boolean | undefined {
  if (suffix === "/service_requests") {
    return access.has("can_read_service_request");
  }
  if (suffix === "/inventory/summary") {
    return access.has("can_read_inventory_item");
  }
  if (
    suffix === "/inventory/internal/receive/" ||
    suffix === "/inventory/internal/dispatch/"
  ) {
    return access.has("can_read_supply_delivery");
  }
  if (suffix.startsWith("/inventory/external/")) {
    return access.has("can_read_supply_request");
  }
  if (
    suffix === "/medication_requests" ||
    suffix === "/medication_dispense" ||
    suffix === "/medication_return"
  ) {
    return false; // dispense orders are closed to restricted users
  }
  return undefined;
}

/** The slug that lets this user open some place page, if any (also gates the My places item). */
function placeSlug(access: IhrAccess): string | undefined {
  for (const page of PLACE_PAGES) {
    const allowed = locationLeafRule(page, access);
    if (allowed) {
      return page === "/service_requests"
        ? "can_read_service_request"
        : "can_read_inventory_item";
    }
  }
  return undefined;
}

/**
 * The first page of a location this user may open, as a path under the location
 * (`/service_requests`, `/inventory/summary`), or undefined when none.
 */
export function ihrPlacePage(access: IhrAccess): string | undefined {
  return PLACE_PAGES.find((page) => locationLeafRule(page, access));
}

function locationTopRule(
  suffix: string,
  access: IhrAccess,
): "keep" | "hide" | "show" | "group" | "leaf" {
  // CARE 3.1 (P2-T0b): Laboratory is now a flat item (Service requests), no parent. The
  // location overview is a landing page of the location's own details and links; Responses
  // lists the location's form responses (clinical forms): hidden, fails closed.
  if (suffix === "/overview") return "keep";
  if (suffix === "/responses") return "hide";
  if (suffix === "/beds" || suffix === "/pharmacy") return "hide";
  if (suffix === "/service_requests") return "leaf";
  if (suffix === "/inventory/summary") return "group";
  if (suffix === "/schedule") {
    return access.has("can_list_schedule") ? "show" : "hide";
  }
  if (suffix === "/appointments" || suffix === "/queues") {
    return access.has("can_list_booking") ? "show" : "hide";
  }
  return "keep";
}

/** CARE 3.1's location Overview page (P2-T0b review): its quick links and its Forms section
 * (submit forms, responses) as this user may use them. A restricted role gets a link only
 * where its location menu shows that page; an unknown link fails closed, as does pending. */
export function ihrLocationOverview(access: IhrAccess): {
  allows: (path: string) => boolean;
  forms: boolean;
} {
  if (access.mode === "pending") return { allows: () => false, forms: false };
  if (access.mode !== "restricted") return { allows: () => true, forms: true };
  const allows = (path: string) => {
    const leaf = locationLeafRule(path, access);
    if (leaf !== undefined) return leaf === true;
    const top = locationTopRule(path, access);
    return top === "show";
  };
  return { allows, forms: false };
}

/** CARE's location links, as this user may use them (none while pending; as CARE has them in care mode). */
export function ihrLocationLinks<T extends LinkLike>(
  links: T[],
  access: IhrAccess,
  baseUrl: string,
): T[] {
  if (access.mode === "pending") {
    return links.map((link) => setVisible(link, false));
  }
  if (access.mode !== "restricted") return links;
  return links.map((link) => {
    const suffix = suffixOf(link.url, baseUrl);
    switch (locationTopRule(suffix, access)) {
      case "hide":
        return setVisible(link, false);
      case "show":
        return setVisible(link, true);
      case "leaf":
        return setVisible(link, locationLeafRule(suffix, access) === true);
      case "group": {
        const children = mapChildren(link, (child) =>
          locationLeafRule(suffixOf(child.url, baseUrl), access),
        );
        return withChildren(link, children);
      }
      default:
        return link;
    }
  });
}
