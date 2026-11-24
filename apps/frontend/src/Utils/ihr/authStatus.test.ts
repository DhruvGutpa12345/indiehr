// IndieHR P2-T0c (docs/reviews/p2-t0c/design.md §5.1): the one session status the router's
// guards and the rendered chrome both use.
// Run (from apps/frontend): node --import tsx --test src/Utils/ihr/authStatus.test.ts
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { type IhrAuthInput, ihrAuthStatus } from "./authStatus";

const PATIENT = { token: "otp-token" };

function status(over: Partial<IhrAuthInput>) {
  return ihrAuthStatus({
    pendingSignOut: false,
    user: undefined,
    patientToken: null,
    ...over,
  });
}

describe("ihrAuthStatus", () => {
  it("a sign-out the backend has not confirmed: never staff", () => {
    assert.equal(
      status({
        pendingSignOut: true,
        user: { status: "success", hasData: true },
      }),
      "anonymous",
    );
    assert.equal(
      status({
        pendingSignOut: true,
        user: { status: "success", hasData: true },
        patientToken: PATIENT,
      }),
      "patient",
    );
    assert.equal(status({ pendingSignOut: true }), "anonymous");
  });

  it("the current user loaded: staff, also over a patient token", () => {
    assert.equal(
      status({ user: { status: "success", hasData: true } }),
      "user",
    );
    assert.equal(
      status({
        user: { status: "success", hasData: true },
        patientToken: PATIENT,
      }),
      "user",
    );
  });

  it("a refetch error keeps the data: still staff", () => {
    assert.equal(status({ user: { status: "error", hasData: true } }), "user");
  });

  it("no answer yet: loading", () => {
    assert.equal(status({ user: undefined }), "loading");
    assert.equal(
      status({ user: { status: "pending", hasData: false } }),
      "loading",
    );
    assert.equal(
      status({
        user: { status: "pending", hasData: false },
        patientToken: PATIENT,
      }),
      "loading",
    );
  });

  it("the backend said not signed in: patient or anonymous", () => {
    assert.equal(
      status({ user: { status: "error", hasData: false } }),
      "anonymous",
    );
    assert.equal(
      status({
        user: { status: "error", hasData: false },
        patientToken: PATIENT,
      }),
      "patient",
    );
    assert.equal(
      status({
        user: { status: "error", hasData: false },
        patientToken: { token: "" },
      }),
      "anonymous",
    );
    assert.equal(
      status({ user: { status: "success", hasData: false } }),
      "anonymous",
    );
  });
});
