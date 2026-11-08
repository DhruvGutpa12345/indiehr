import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { TZ_HEADER, deviceTimeZone, offsetZone } from "./deviceTimeZone";

describe("device time zone header (P1-T7b)", () => {
  it("writes the offset east of UTC, as the backend expects", () => {
    assert.equal(offsetZone(-330), "UTC+05:30");
    assert.equal(offsetZone(0), "UTC+00:00");
    assert.equal(offsetZone(-345), "UTC+05:45");
    assert.equal(offsetZone(300), "UTC-05:00");
    assert.equal(offsetZone(210), "UTC-03:30");
  });

  it("prefers the browser's zone name and falls back to the offset", () => {
    assert.equal(deviceTimeZone("Asia/Kolkata", -330), "Asia/Kolkata");
    assert.equal(deviceTimeZone("Europe/London", 0), "Europe/London");
    assert.equal(deviceTimeZone("", -330), "UTC+05:30");
    assert.equal(deviceTimeZone("", 0), "UTC+00:00");
  });

  it("never sends more than the backend accepts", () => {
    assert.equal(deviceTimeZone("Z".repeat(100), 0).length, 64);
  });

  it("reports some zone on this machine", () => {
    assert.ok(deviceTimeZone().length > 0);
    assert.equal(TZ_HEADER, "X-IHR-TZ");
  });
});
