// IndieHR (P1-T1d): the browser specs' code generator against RFC 6238's published SHA-1
// vectors (secret "12345678901234567890" as ASCII bytes, as Keycloak uses its secret). The last
// six digits of the RFC's eight-digit codes are the six-digit codes. No secret of ours is used.
// Run: node --import tsx --test tests-ihr/lib/totp.test.ts
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
  clearRefused,
  codeForStep,
  markRefused,
  refusedEarlier,
  stepAt,
} from "./totp";

const RFC_SEED = "12345678901234567890";
const VECTORS: [number, string][] = [
  [59, "94287082"],
  [1111111109, "07081804"],
  [1111111111, "14050471"],
  [1234567890, "89005924"],
  [2000000000, "69279037"],
  [20000000000, "65353130"],
];

test("RFC 6238 SHA-1 vectors (six-digit form)", () => {
  for (const [seconds, eight] of VECTORS) {
    assert.equal(
      codeForStep(RFC_SEED, stepAt(seconds * 1000)),
      eight.slice(-6),
      `T=${seconds}`,
    );
  }
});

test("steps are 30 seconds", () => {
  assert.equal(stepAt(0), 0);
  assert.equal(stepAt(29_999), 0);
  assert.equal(stepAt(30_000), 1);
});

test("agrees with our Python rule (the one the live Keycloak tests prove)", () => {
  // A made-up secret in Keycloak's own shape (20 hex characters); never a test user's.
  const madeUp = ["0f1e2d3c4b", "5a69788796"].join("");
  const at = 1_800_000_015; // seconds
  const python = execFileSync(
    "python3",
    [
      "-c",
      "import sys; sys.path.insert(0, sys.argv[1]);" +
        " from ihr_platform.keycloak import totp;" +
        " print(totp.code(sys.argv[2], at=float(sys.argv[3])))",
      join(__dirname, "..", "..", "..", "..", "plugins", "ihr_platform"),
      madeUp,
      String(at),
    ],
    { encoding: "utf8" },
  ).trim();
  assert.equal(codeForStep(madeUp, stepAt(at * 1000)), python);
});

test("a new run clears only the refused flag, keeping the used steps", () => {
  const before = process.env.IHR_E2E_STATE;
  const path = join(mkdtempSync(join(tmpdir(), "ihr-totp-")), "state.json");
  process.env.IHR_E2E_STATE = path;
  try {
    clearRefused(); // no file yet: nothing to do, nothing written
    writeFileSync(path, JSON.stringify({ used: { "made.up": 42 } }));
    markRefused("made.up");
    assert.equal(refusedEarlier(), "made.up");
    clearRefused();
    assert.equal(refusedEarlier(), undefined);
    assert.deepEqual(JSON.parse(readFileSync(path, "utf8")), {
      used: { "made.up": 42 },
    });
  } finally {
    if (before === undefined) delete process.env.IHR_E2E_STATE;
    else process.env.IHR_E2E_STATE = before;
  }
});
