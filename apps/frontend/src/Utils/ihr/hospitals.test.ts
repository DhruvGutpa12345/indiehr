// IndieHR P1-T31 (docs/reviews/p1-t31/design.md §4, attack.md D4, D10, D12, R7): the hospital
// switcher's rules. Run: node --import tsx --test src/Utils/ihr/hospitals.test.ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

import {
  HOSPITALS_STALE_MS,
  SWITCH_PATH,
  entryKind,
  hospitalsQueryKey,
  menuEntries,
  switcherEntries,
  type HospitalEntry,
} from "./hospitals";

const alpha: HospitalEntry = {
  name: "Alpha General Hospital",
  url: "http://alpha.localhost:8080/api/ihr/auth/login/",
  current: true,
  available: true,
};
const beta: HospitalEntry = {
  name: "Beta Care Hospital",
  url: "http://beta.localhost:8080/api/ihr/auth/login/",
  current: false,
  available: true,
};
const HTTP = "http:";
const HTTPS = "https:";

describe("the group shows from two entries (Q-K146)", () => {
  it("is null for nothing, one entry, or a malformed answer", () => {
    assert.equal(switcherEntries(undefined, HTTP), null);
    assert.equal(switcherEntries(null, HTTP), null);
    assert.equal(switcherEntries({}, HTTP), null);
    assert.equal(switcherEntries({ hospitals: "x" }, HTTP), null);
    assert.equal(switcherEntries({ hospitals: [] }, HTTP), null);
    assert.equal(switcherEntries({ hospitals: [alpha] }, HTTP), null);
  });
  it("lists two entries", () => {
    const got = switcherEntries({ hospitals: [alpha, beta] }, HTTP);
    assert.deepEqual(got, [alpha, beta]);
  });
  it("counts only the entries that pass the rules", () => {
    const bad = { ...beta, url: "javascript:alert(1)" };
    assert.equal(switcherEntries({ hospitals: [alpha, bad] }, HTTP), null);
  });
});

describe("an entry is accepted only as the server builds it (D4, D10)", () => {
  const dropped = (over: Partial<HospitalEntry>, protocol = HTTP) =>
    switcherEntries(
      { hospitals: [alpha, beta, { ...beta, name: "Gamma", ...over }] },
      protocol,
    )?.some((e) => e.name === "Gamma");

  it("keeps a well-formed third entry (control)", () => {
    assert.equal(
      dropped({ url: "http://gamma.localhost:8080/api/ihr/auth/login/" }),
      true,
    );
  });
  it("drops javascript: and other schemes", () => {
    assert.ok(!dropped({ url: "javascript:alert(1)" }));
    assert.ok(!dropped({ url: "data:text/html,x" }));
    assert.ok(!dropped({ url: "ftp://gamma.localhost/api/ihr/auth/login/" }));
  });
  it("drops credentials in the address", () => {
    assert.ok(
      !dropped({
        url: "http://user:pw@gamma.localhost:8080/api/ihr/auth/login/",
      }),
    );
    assert.ok(
      !dropped({ url: "http://user@gamma.localhost:8080/api/ihr/auth/login/" }),
    );
  });
  it("drops any other path, including a lookalike", () => {
    assert.ok(!dropped({ url: "http://gamma.localhost:8080/" }));
    assert.ok(
      !dropped({ url: "http://gamma.localhost:8080/api/ihr/auth/logout/" }),
    );
    assert.ok(
      !dropped({ url: "http://gamma.localhost:8080/api/ihr/auth/login" }),
    );
    assert.ok(
      !dropped({
        url: "http://gamma.localhost:8080/x/../api/ihr/auth/login/z",
      }),
    );
    assert.ok(
      !dropped({
        url: "http://gamma.localhost:8080/api/ihr/auth/login/extra",
      }),
    );
  });
  it("drops a query or a fragment, even an empty one", () => {
    assert.ok(
      !dropped({
        url: "http://gamma.localhost:8080/api/ihr/auth/login/?next=%2F",
      }),
    );
    assert.ok(
      !dropped({ url: "http://gamma.localhost:8080/api/ihr/auth/login/?" }),
    );
    assert.ok(
      !dropped({ url: "http://gamma.localhost:8080/api/ihr/auth/login/#x" }),
    );
    assert.ok(
      !dropped({ url: "http://gamma.localhost:8080/api/ihr/auth/login/#" }),
    );
  });
  it("drops http on an https page, and https on an http page", () => {
    const https = (host: string) => `https://${host}${SWITCH_PATH}`;
    const page = [
      { ...alpha, url: https("alpha.app.example.in") },
      { ...beta, url: https("beta.app.example.in") },
      {
        ...beta,
        name: "Gamma",
        url: `http://gamma.app.example.in${SWITCH_PATH}`,
      },
    ];
    const got = switcherEntries({ hospitals: page }, HTTPS);
    assert.deepEqual(
      got?.map((e) => e.name),
      [alpha.name, beta.name],
    );
    // the same answer on an http page: nothing but the one http entry remains, so no group
    assert.equal(switcherEntries({ hospitals: page }, HTTP), null);
  });
  it("accepts a custom domain (no sibling-host rule)", () => {
    assert.equal(
      dropped({ url: "http://care.example.org/api/ihr/auth/login/" }),
      true,
    );
  });
  it("drops an unparseable address, a missing name and a non-object entry", () => {
    assert.ok(!dropped({ url: "not a url" }));
    assert.ok(!dropped({ url: "" }));
    assert.equal(
      switcherEntries(
        { hospitals: [alpha, beta, null, "x", { url: beta.url }] },
        HTTP,
      )?.length,
      2,
    );
    assert.equal(
      switcherEntries({ hospitals: [alpha, { ...beta, name: "  " }] }, HTTP),
      null,
    );
  });
  it("drops a second entry with the same address", () => {
    assert.equal(
      switcherEntries(
        { hospitals: [alpha, { ...alpha, name: "Alpha again" }] },
        HTTP,
      ),
      null,
    );
  });
  it("reads flags strictly: only true counts", () => {
    const got = switcherEntries(
      {
        hospitals: [
          alpha,
          {
            ...beta,
            available: "yes" as unknown as boolean,
            current: 1 as unknown as boolean,
          },
        ],
      },
      HTTP,
    );
    assert.deepEqual(
      got?.map((e) => [e.current, e.available]),
      [
        [true, true],
        [false, false],
      ],
    );
  });
});

describe("the order is the current one first, then by name", () => {
  it("puts the current hospital first whatever the answer's order", () => {
    const got = switcherEntries({ hospitals: [beta, alpha] }, HTTP);
    assert.deepEqual(
      got?.map((e) => e.name),
      [alpha.name, beta.name],
    );
  });
  it("sorts the others by name, ignoring case", () => {
    const named = (name: string) => ({
      ...beta,
      name,
      url: `http://${name.toLowerCase().replace(/\W/g, "")}.localhost:8080${SWITCH_PATH}`,
    });
    const got = switcherEntries(
      {
        hospitals: [named("zeta"), alpha, named("Delta"), named("alfa")],
      },
      HTTP,
    );
    assert.deepEqual(
      got?.map((e) => e.name),
      [alpha.name, "alfa", "Delta", "zeta"],
    );
  });
});

describe("each entry is current, unavailable or other", () => {
  it("names the kind for the test hook", () => {
    assert.equal(entryKind(alpha), "current");
    assert.equal(entryKind(beta), "other");
    assert.equal(entryKind({ ...beta, available: false }), "unavailable");
    // the current one is never offered as a target, even when it is marked unavailable too
    assert.equal(entryKind({ ...alpha, available: false }), "current");
  });
});

describe("a passive menu stays silent on any problem (R7)", () => {
  const answer = { hospitals: [alpha, beta] };
  it("renders nothing on an error, even with older data", () => {
    assert.equal(menuEntries({ data: answer, isError: true }, HTTP), null);
    assert.equal(menuEntries({ data: undefined, isError: true }, HTTP), null);
  });
  it("renders nothing while loading", () => {
    assert.equal(menuEntries({ data: undefined, isError: false }, HTTP), null);
  });
  it("renders the list on an answer", () => {
    assert.deepEqual(
      menuEntries({ data: answer, isError: false }, HTTP)?.length,
      2,
    );
  });
});

describe("the query is per person and never persisted (D12, Q-K149, F10)", () => {
  it("has the username in its key", () => {
    assert.deepEqual(hospitalsQueryKey("dr.meera"), [
      "ihr",
      "my-hospitals",
      "dr.meera",
    ]);
    assert.notDeepEqual(
      hospitalsQueryKey("dr.meera"),
      hospitalsQueryKey("nurse.asha"),
    );
  });
  it("is cached for five minutes", () => {
    assert.equal(HOSPITALS_STALE_MS, 5 * 60 * 1000);
  });
  const component = readFileSync(
    join(process.cwd(), "src", "components", "ihr", "IhrHospitalsMenu.tsx"),
    "utf-8",
  );
  it("the component asks silently, with the user-scoped key, retry off", () => {
    assert.match(component, /silent:\s*true/);
    assert.match(component, /hospitalsQueryKey\(\s*user\.username\s*\)/);
    assert.match(component, /retry:\s*false/);
    assert.match(component, /staleTime:\s*HOSPITALS_STALE_MS/);
  });
  it("the component never marks the query for the local-storage cache", () => {
    assert.doesNotMatch(component, /persist/);
  });
  it("the component leaves only by a top-level navigation to a checked entry", () => {
    assert.match(component, /window\.location\.assign\(/);
    assert.doesNotMatch(component, /window\.open|postMessage/);
    assert.doesNotMatch(component, /localStorage|sessionStorage/);
  });
});

describe("the strings exist in English and Hindi", () => {
  for (const lang of ["en", "hi"]) {
    const strings = JSON.parse(
      readFileSync(
        join(process.cwd(), "..", "..", "deploy", "locale", `${lang}.json`),
        "utf-8",
      ),
    ) as Record<string, string>;
    it(`${lang}: the four keys, and the aria text names the hospital`, () => {
      for (const key of [
        "ihr_hospitals_menu",
        "ihr_hospitals_current",
        "ihr_hospitals_unavailable",
        "ihr_hospitals_switch_aria",
      ]) {
        assert.ok(strings[key], `${lang} ${key}`);
      }
      assert.ok(strings.ihr_hospitals_switch_aria.includes("{{name}}"));
    });
  }
});
