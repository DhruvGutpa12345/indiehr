import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import {
  COOKIE_SESSION,
  authorizationHeaders,
  sessionAuthorization,
} from "./appearanceAuth";

describe("appearance saves on a cookie session (P2-T0b review F2)", () => {
  it("uses the marker when there is no bearer token", () => {
    assert.equal(sessionAuthorization(null), COOKIE_SESSION);
    assert.equal(sessionAuthorization("Bearer x"), "Bearer x");
  });

  it("sends no Authorization header for the cookie session", () => {
    assert.deepEqual(authorizationHeaders(COOKIE_SESSION), {});
    assert.deepEqual(authorizationHeaders("Bearer x"), {
      Authorization: "Bearer x",
    });
  });

  it("CARE's save uses them (no 'No authenticated session' without a token)", () => {
    const src = readFileSync(
      new URL("../../context/AppearanceContext.tsx", import.meta.url),
      "utf8",
    );
    assert.match(src, /sessionAuthorization\(getAuthorizationHeader\(\)\)/);
    assert.doesNotMatch(src, /Authorization: authorization \}/);
    assert.doesNotMatch(src, /Authorization: session\.authorization \}/);
    assert.equal((src.match(/authorizationHeaders\(/g) ?? []).length, 2);
  });
});
