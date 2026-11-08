import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  autofillTarget,
  completePincode,
  lineage,
  pickFromPlaces,
  type PincodePlace,
  type PlaceOrganization,
} from "./pincodeRules";

// Synthetic geography: Delhi with two districts, Maharashtra with Pune and a ward in Pune
const delhi: PlaceOrganization = { id: "s-07" };
const central: PlaceOrganization = { id: "d-central", parent: { id: "s-07" } };
const newDelhi: PlaceOrganization = { id: "d-new", parent: { id: "s-07" } };
const maha: PlaceOrganization = { id: "s-27" };
const pune: PlaceOrganization = { id: "d-pune", parent: { id: "s-27" } };
const ward: PlaceOrganization = {
  id: "w-1",
  parent: { id: "d-pune", parent: { id: "s-27" } },
};

function place(
  code: string,
  district: string,
  state: PlaceOrganization | null,
  org: PlaceOrganization | null,
): PincodePlace {
  return {
    state_code: code,
    state: code === "07" ? "Delhi" : "Maharashtra",
    district,
    state_organization: state?.id ?? null,
    geo_organization: org?.id ?? null,
    state_organization_object: state,
    geo_organization_object: org,
  };
}

const PUNE = [place("27", "Pune", maha, pune)];
const DELHI_110001 = [
  place("07", "Central Delhi", delhi, central),
  place("07", "New Delhi", delhi, newDelhi),
];

describe("PIN code auto-fill rules", () => {
  it("accepts only complete 6-digit PIN codes", () => {
    assert.equal(completePincode(411001), "411001");
    assert.equal(completePincode(" 110001 "), "110001");
    assert.equal(completePincode(41100), null);
    assert.equal(completePincode("011000"), null);
    assert.equal(completePincode(4110012), null);
    assert.equal(completePincode(undefined), null);
    assert.equal(completePincode(null), null);
    assert.equal(completePincode(""), null);
  });

  it("fills the district when the PIN has one", () => {
    assert.deepEqual(pickFromPlaces(PUNE), { kind: "district", org: pune });
  });

  it("fills the state and offers districts for a multi-district PIN", () => {
    assert.deepEqual(pickFromPlaces(DELHI_110001), {
      kind: "state",
      org: delhi,
      choices: [central, newDelhi],
    });
  });

  it("fills the state when this hospital lacks the district", () => {
    assert.deepEqual(pickFromPlaces([place("27", "Pune", maha, null)]), {
      kind: "state",
      org: maha,
      choices: [],
    });
    // one district known, one not: the district is not certain
    assert.deepEqual(
      pickFromPlaces([
        place("07", "Central Delhi", delhi, central),
        place("07", "New Delhi", delhi, null),
      ]),
      { kind: "state", org: delhi, choices: [central] },
    );
  });

  it("fills nothing across states, without a state, or without places", () => {
    assert.equal(pickFromPlaces([...PUNE, ...DELHI_110001]), null);
    assert.equal(pickFromPlaces([place("27", "Pune", null, null)]), null);
    assert.equal(pickFromPlaces([]), null);
    assert.equal(pickFromPlaces(undefined), null);
  });

  it("walks an organisation's parents", () => {
    assert.deepEqual(lineage(ward), ["w-1", "d-pune", "s-27"]);
    assert.deepEqual(lineage(delhi), ["s-07"]);
    assert.deepEqual(lineage(null), []);
  });

  it("fills an empty form", () => {
    assert.equal(autofillTarget(pickFromPlaces(PUNE), null), pune);
    assert.equal(autofillTarget(pickFromPlaces(DELHI_110001), null), delhi);
    assert.equal(autofillTarget(null, null), null);
  });

  it("keeps a choice that already fits the PIN", () => {
    const one = pickFromPlaces(PUNE);
    assert.equal(autofillTarget(one, pune), null);
    assert.equal(autofillTarget(one, ward), null); // inside the district
    const two = pickFromPlaces(DELHI_110001);
    assert.equal(autofillTarget(two, newDelhi), null);
    assert.equal(autofillTarget(two, delhi), null);
    const unknownDistrict = pickFromPlaces([place("27", "Pune", maha, null)]);
    assert.equal(autofillTarget(unknownDistrict, ward), null);
  });

  it("replaces a choice that does not fit the PIN", () => {
    assert.equal(autofillTarget(pickFromPlaces(PUNE), newDelhi), pune);
    assert.equal(autofillTarget(pickFromPlaces(PUNE), maha), pune);
    assert.equal(autofillTarget(pickFromPlaces(DELHI_110001), pune), delhi);
  });
});
