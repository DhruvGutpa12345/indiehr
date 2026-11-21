// IndieHR P1-T24b (docs/reviews/p1-t24/design.md "T24b as decided", attack.md "Decision"): the
// staff "Sign-in" card's rules. Run: node --import tsx --test src/Utils/ihr/staffAccess.test.ts
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  MAX_POLLS,
  POLL_MS,
  type StaffAccess,
  actionLabelKey,
  cardVisible,
  confirmNeeded,
  formatIst,
  offerButtons,
  pollDelay,
  problemKey,
  problemShown,
  refusalKey,
  requestStatusKey,
  stateLine,
  wantsCard,
} from "./staffAccess";

const answer = (over: Partial<StaffAccess> = {}): StaffAccess => ({
  active: true,
  state: "active",
  offers: ["reset-sign-in"],
  invitation: null,
  requests: [],
  ...over,
});

describe("formatIst", () => {
  it("shows DD-MM-YYYY HH:MM in India time", () => {
    // 20-11-2026 08:35 UTC = 14:05 IST
    assert.equal(formatIst("2026-11-20T08:35:00Z"), "20-11-2026 14:05 IST");
  });
  it("rolls over midnight into the next IST day", () => {
    assert.equal(
      formatIst("2026-12-31T19:00:00+00:00"),
      "01-01-2027 00:30 IST",
    );
  });
  it("reads an offset, not the browser's zone", () => {
    assert.equal(
      formatIst("2026-11-21T10:00:00+05:30"),
      "21-11-2026 10:00 IST",
    );
  });
  it("gives an empty text for nothing or a bad date", () => {
    assert.equal(formatIst(null), "");
    assert.equal(formatIst(undefined), "");
    assert.equal(formatIst("not a date"), "");
  });
});

describe("the card shows only with offers (G2, H2, A5)", () => {
  it("is hidden with no offers, and with no answer", () => {
    assert.equal(cardVisible(answer({ offers: [] }), false), false);
    assert.equal(cardVisible(undefined, false), false);
  });
  it("is shown with an offer", () => {
    assert.equal(cardVisible(answer(), false), true);
  });
  it("stays once an action of ours was asked, so its outcome is seen", () => {
    assert.equal(cardVisible(answer({ offers: [] }), true), true);
    assert.equal(cardVisible(undefined, true), false);
  });
  it("is asked for only by a care-mode viewer on someone else", () => {
    const base = {
      mode: "care",
      isOwn: false,
      isServiceAccount: false,
      hasUsername: true,
    } as const;
    assert.equal(wantsCard(base), true);
    assert.equal(wantsCard({ ...base, mode: "restricted" }), false);
    assert.equal(wantsCard({ ...base, mode: "pending" }), false);
    assert.equal(wantsCard({ ...base, isOwn: true }), false);
    assert.equal(wantsCard({ ...base, isServiceAccount: true }), false);
    assert.equal(wantsCard({ ...base, hasUsername: false }), false);
  });
});

describe("offerButtons", () => {
  it("lists only what the backend offered, in a fixed order", () => {
    assert.deepEqual(
      offerButtons(
        answer({ offers: ["cancel-invitation", "resend-invitation"] }),
      ),
      ["resend-invitation", "cancel-invitation"],
    );
  });
  it("drops an action this build does not know", () => {
    assert.deepEqual(
      offerButtons(answer({ offers: ["delete-everything", "reset-sign-in"] })),
      ["reset-sign-in"],
    );
  });
  it("gives no button without offers (reset is never a default)", () => {
    assert.deepEqual(offerButtons(answer({ offers: [] })), []);
    assert.deepEqual(offerButtons(undefined), []);
  });
});

describe("confirmNeeded", () => {
  it("asks before a reset and a cancel, not before sending again", () => {
    assert.equal(confirmNeeded("reset-sign-in"), true);
    assert.equal(confirmNeeded("cancel-invitation"), true);
    assert.equal(confirmNeeded("resend-invitation"), false);
  });
});

describe("stateLine: a fixed phrase from the state (A4)", () => {
  it("names every state the backend sends", () => {
    for (const state of [
      "active",
      "awaiting-setup",
      "queued",
      "sent",
      "failed",
      "cancelled",
      "expired",
      "none",
    ]) {
      const line = stateLine(answer({ state }));
      assert.match(line.key, /^ihr_staff_state_/);
    }
  });
  it("puts the sent and expiry times in IST", () => {
    const line = stateLine(
      answer({
        state: "sent",
        invitation: {
          status: "sent",
          kind: "new",
          sent_at: "2026-11-20T08:35:00Z",
          expires_at: "2026-11-23T08:35:00Z",
          problem: null,
        },
      }),
    );
    assert.equal(line.key, "ihr_staff_state_sent");
    assert.deepEqual(line.params, {
      sent: "20-11-2026 14:05 IST",
      expires: "23-11-2026 14:05 IST",
    });
  });
  it("has a plain phrase when the times are missing", () => {
    const line = stateLine(answer({ state: "sent", invitation: null }));
    assert.equal(line.key, "ihr_staff_state_sent_plain");
  });
  it("an unknown state is the neutral phrase, never raw text", () => {
    assert.equal(
      stateLine(answer({ state: "something <b>new</b>" })).key,
      "ihr_staff_state_none",
    );
  });
});

describe("problemKey: codes, never worker text", () => {
  it("maps every backend code to its own phrase", () => {
    const codes = [
      "hospital-not-live",
      "waiting-for-setup",
      "user-gone",
      "account-gone",
      "already-active",
      "already-set-up",
      "linked-elsewhere",
      "no-invitation",
      "not-active",
      "not-replaced",
      "protected",
      "try-later",
      "not-allowed",
      "failed",
    ];
    const keys = codes.map(problemKey);
    assert.equal(new Set(keys).size, codes.length);
    for (const key of keys) assert.match(key ?? "", /^ihr_staff_problem_/);
  });
  it("shows no problem when there is none", () => {
    assert.equal(problemKey(null), null);
    assert.equal(problemKey(undefined), null);
    assert.equal(problemKey(""), null);
  });
  it("shows the generic failure for a code it does not know", () => {
    assert.equal(
      problemKey("GET /admin/realms/x/users/9 -> 500"),
      "ihr_staff_problem_failed",
    );
  });
  it("the protected phrase does not say why or where", () => {
    assert.equal(problemKey("protected"), "ihr_staff_problem_protected");
  });
});

describe("labels", () => {
  it("names each request action and status; others are generic", () => {
    assert.equal(actionLabelKey("reset_sign_in"), "ihr_staff_reset");
    assert.equal(actionLabelKey("resend_invitation"), "ihr_staff_resend");
    assert.equal(actionLabelKey("cancel_invitation"), "ihr_staff_cancel");
    assert.equal(actionLabelKey("x"), "ihr_staff_action_other");
    assert.equal(requestStatusKey("queued"), "ihr_staff_request_queued");
    assert.equal(requestStatusKey("running"), "ihr_staff_request_running");
    assert.equal(requestStatusKey("done"), "ihr_staff_request_done");
    assert.equal(requestStatusKey("failed"), "ihr_staff_request_failed");
    assert.equal(requestStatusKey("zzz"), "ihr_staff_request_other");
  });
});

describe("pollDelay: every 10 s while being sent, at most 6 times", () => {
  it("polls while the invitation is queued", () => {
    assert.equal(pollDelay(answer({ state: "queued" }), 0), POLL_MS);
    assert.equal(POLL_MS, 10_000);
  });
  it("polls while our request is waiting or running", () => {
    const waiting = answer({
      requests: [
        {
          action: "reset_sign_in",
          status: "running",
          requested_at: "2026-11-21T06:30:00Z",
          problem: null,
        },
      ],
    });
    assert.equal(pollDelay(waiting, 2), POLL_MS);
  });
  it("stops after six polls", () => {
    assert.equal(pollDelay(answer({ state: "queued" }), MAX_POLLS), false);
    assert.equal(MAX_POLLS, 6);
  });
  it("does not poll a settled card or an empty answer", () => {
    assert.equal(pollDelay(answer(), 0), false);
    assert.equal(pollDelay(undefined, 0), false);
  });
});

describe("refusalKey: a 400's code chooses our own phrase", () => {
  it("maps every code the backend sends (staff_access.Refusal)", () => {
    const keys: Record<string, string> = {
      own: "ihr_staff_error_own",
      "not-active-yet": "ihr_staff_error_not_active",
      "reset-recent": "ihr_staff_error_reset_recent",
      "protected-recent": "ihr_staff_error_protected_recent",
      "already-active": "ihr_staff_error_already_active",
      "being-sent": "ihr_staff_error_being_sent",
      "no-invitation": "ihr_staff_error_no_invitation",
      "already-asked": "ihr_staff_error_already_asked",
    };
    for (const [code, key] of Object.entries(keys)) {
      assert.equal(refusalKey({ code, detail: "English text" }), key, code);
    }
  });
  it("never shows the backend's English: unknown, missing or malformed is the generic phrase", () => {
    const generic = "ihr_staff_error_request";
    assert.equal(refusalKey({ code: "unknown-action", detail: "x" }), generic);
    assert.equal(refusalKey({ code: "brand-new", detail: "x" }), generic);
    assert.equal(refusalKey({ detail: "Already asked." }), generic);
    assert.equal(refusalKey(["Already asked."]), generic);
    assert.equal(refusalKey(undefined), generic);
    assert.equal(refusalKey({ code: 5 }), generic);
    // a prototype name is not a code
    assert.equal(refusalKey({ code: "constructor" }), generic);
  });
});

describe("problemShown: only where a problem still applies", () => {
  it("is shown while the invitation is open or failed", () => {
    for (const state of ["queued", "sent", "failed"]) {
      assert.equal(problemShown(state), true, state);
    }
  });
  it("is hidden once cancelled or expired, and for people who have no invitation", () => {
    for (const state of [
      "cancelled",
      "expired",
      "active",
      "awaiting-setup",
      "none",
      "something-new",
    ]) {
      assert.equal(problemShown(state), false, state);
    }
  });
});

describe("the new problem code", () => {
  it("a failed wait has its own phrase, not waiting-for-setup", () => {
    assert.equal(
      problemKey("other-account-not-set-up"),
      "ihr_staff_problem_other_account_not_set_up",
    );
    assert.notEqual(
      problemKey("other-account-not-set-up"),
      problemKey("waiting-for-setup"),
    );
  });
});
