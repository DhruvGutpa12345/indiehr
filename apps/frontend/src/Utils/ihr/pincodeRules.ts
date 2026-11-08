// IndieHR (P1-T7b): pure rules for filling state and district from a PIN code, kept free of
// browser and app imports so they can be unit-tested with plain Node. The data comes from
// our own API (/api/ihr/pincode/<pin>/, India Post's directory loaded on our server); no
// external host is ever called.

/** The parts of an organisation these rules need (CARE's `Organization` fits). */
export interface PlaceOrganization {
  id: string;
  parent?: PlaceParent | null;
}

interface PlaceParent {
  id: string;
  parent?: PlaceParent | null;
}

/** One place a PIN code covers, as /api/ihr/pincode/<pin>/ returns it. */
export interface PincodePlace<O extends PlaceOrganization = PlaceOrganization> {
  state_code: string;
  state: string;
  district: string;
  state_organization: string | null;
  geo_organization: string | null;
  state_organization_object?: O | null;
  geo_organization_object?: O | null;
}

/** One district: fill state and district. */
interface DistrictPick<O> {
  kind: "district";
  org: O;
}

/** One state, district not certain: fill the state, offer the districts found. */
interface StatePick<O> {
  kind: "state";
  org: O;
  choices: O[];
}

export type PincodePick<O extends PlaceOrganization = PlaceOrganization> =
  DistrictPick<O> | StatePick<O>;

const PIN_RE = /^[1-9]\d{5}$/;

/** The PIN as 6 digits, or null when it is not a complete Indian PIN code. */
export function completePincode(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  return PIN_RE.test(text) ? text : null;
}

/**
 * What a PIN code's places can fill:
 * - one place with a district of this hospital → that district;
 * - several districts in one state, or a district this hospital does not have → the
 *   state, with the districts that were found as quick choices;
 * - places in different states, or no state of this hospital → nothing.
 */
export function pickFromPlaces<O extends PlaceOrganization>(
  places: PincodePlace<O>[] | null | undefined,
): PincodePick<O> | null {
  if (!places?.length) return null;
  const states = new Set(places.map((place) => place.state_code));
  if (states.size !== 1) return null;
  const state = places.map((p) => p.state_organization_object).find(Boolean);
  const districts: O[] = [];
  for (const place of places) {
    const district = place.geo_organization_object;
    if (district && !districts.some((d) => d.id === district.id)) {
      districts.push(district);
    }
  }
  if (places.length === 1 && districts.length === 1) {
    return { kind: "district", org: districts[0] };
  }
  if (!state) return null;
  return { kind: "state", org: state, choices: districts };
}

/** The ids of an organisation and all its parents, nearest first. */
export function lineage(org: PlaceOrganization | null | undefined): string[] {
  const ids: string[] = [];
  let current: PlaceParent | PlaceOrganization | null | undefined = org;
  while (current && typeof current.id === "string" && ids.length < 20) {
    ids.push(current.id);
    current = current.parent as PlaceParent | null | undefined;
  }
  return ids;
}

/**
 * The organisation to put in the form, or null to leave the form as it is. A choice the
 * user has already made that fits the PIN is kept: the district or a place inside it; when
 * the district is not certain, one of the offered districts (or a place inside one), or
 * anything in the state when no district was found.
 */
export function autofillTarget<O extends PlaceOrganization>(
  pick: PincodePick<O> | null,
  current: PlaceOrganization | null | undefined,
): O | null {
  if (!pick) return null;
  const chosen = lineage(current);
  if (pick.kind === "district") {
    return chosen.includes(pick.org.id) ? null : pick.org;
  }
  const keep = pick.choices.length
    ? chosen[0] === pick.org.id ||
      pick.choices.some((district) => chosen.includes(district.id))
    : chosen.includes(pick.org.id);
  return keep ? null : pick.org;
}
