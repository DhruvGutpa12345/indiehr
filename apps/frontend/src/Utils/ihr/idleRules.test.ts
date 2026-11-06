import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  DEFAULT_IDLE,
  idleSecondsSince,
  idleSettingsFrom,
  idleState,
  isOwnApi,
  safeNext,
} from "./idleRules";

const ORIGIN = "http://alpha.localhost:8080";

describe("idle time-out rules", () => {
  it("counts whole seconds since the last input", () => {
    assert.equal(idleSecondsSince(1_000, 61_999), 60);
    assert.equal(idleSecondsSince(null, 5_000), 0);
    assert.equal(idleSecondsSince(10_000, 5_000), 0);
  });

  it("warns before the limit and expires at it", () => {
    const s = { idleSeconds: 1800, warnSeconds: 120 };
    assert.equal(idleState(0, s), "active");
    assert.equal(idleState(1679, s), "active");
    assert.equal(idleState(1680, s), "warn");
    assert.equal(idleState(1800, s), "expired");
  });

  it("takes the hospital's settings and falls back safely", () => {
    assert.deepEqual(
      idleSettingsFrom({ idle_seconds: 120, warn_seconds: 60 }),
      {
        idleSeconds: 120,
        warnSeconds: 60,
      },
    );
    assert.deepEqual(idleSettingsFrom(undefined), DEFAULT_IDLE);
    assert.deepEqual(
      idleSettingsFrom({ idle_seconds: 100, warn_seconds: 500 }),
      {
        idleSeconds: 100,
        warnSeconds: 50,
      },
    );
  });

  it("sends session headers only to our own API", () => {
    assert.ok(isOwnApi("/api/v1/users/", ORIGIN));
    assert.ok(isOwnApi(`${ORIGIN}/api/v1/users/`, ORIGIN));
    assert.ok(!isOwnApi("//evil.test/api", ORIGIN));
    assert.ok(!isOwnApi("https://nominatim.openstreetmap.org/search", ORIGIN));
  });

  it("keeps the return path on this site", () => {
    assert.equal(safeNext(`${ORIGIN}/patients?x=1`, ORIGIN), "/patients?x=1");
    assert.equal(safeNext("/encounters", ORIGIN), "/encounters");
    assert.equal(safeNext("https://evil.test/x", ORIGIN), "/");
    assert.equal(safeNext("//evil.test/x", ORIGIN), "/");
    assert.equal(safeNext(null, ORIGIN), "/");
    assert.equal(safeNext("/.//evil.test/x", ORIGIN), "/evil.test/x");
    assert.equal(safeNext("/a/..//evil.test", ORIGIN), "/evil.test");
  });
});
