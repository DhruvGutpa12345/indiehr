// IndieHR P1-T11c3 (design-c.md §6, §13.3, §13.4). Run: node --import tsx --test src/Utils/ihr/records.test.ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

import {
  FORM_ERROR_KEYS,
  type Grant,
  type GrantSummary,
  LOOK_TIMEOUT_MS,
  MAX_FAILURES_WHILE_HELD,
  REASON_CODES,
  REFERENCE_RE,
  WATCH_START,
  countdownText,
  detailOf,
  detailsKey,
  earliestExpiryMs,
  formatStarted,
  liveGrantFor,
  noticeMinutes,
  patientFromQuery,
  patientIdForGrant,
  reasonLabelKey,
  referenceRequired,
  secondsLeftAt,
  serialLooks,
  serverErrorField,
  timedLook,
  validateRequest,
  watchStep,
  withDetails,
} from "./records";

const PATIENT = "0b1f6a52-7d7e-4a4e-9a52-0d2f1f2f3a4b";

function grant(id: number, secondsLeft: number): Grant {
  return {
    id,
    patient: PATIENT,
    patient_name: "Asha Rao",
    patient_year_of_birth: 1980,
    reason: "insurer",
    reference: "TEST-1",
    created_at: "2026-11-16T08:30:00+00:00",
    expires_at: "2026-11-16T09:00:00+00:00",
    seconds_left: secondsLeft,
  };
}

describe("reasons and reference (Q-K36)", () => {
  it("has the six codes of the backend, with a label key each", () => {
    assert.deepEqual(
      [...REASON_CODES],
      [
        "patient_request",
        "legal",
        "insurer",
        "internal_audit",
        "nabh_review",
        "continuity_of_care",
      ],
    );
    assert.equal(reasonLabelKey("legal"), "ihr_records_reason__legal");
  });

  it("the codes match the backend's RecordsGrant.Reason", () => {
    const models = readFileSync(
      join(
        process.cwd(),
        "..",
        "..",
        "plugins",
        "ihr_platform",
        "ihr_access",
        "models.py",
      ),
      "utf-8",
    );
    const backend = [...models.matchAll(/^\s+[A-Z_]+ = "([a-z_]+)"$/gm)].map(
      (m) => m[1],
    );
    for (const code of REASON_CODES) assert.ok(backend.includes(code), code);
  });

  it("the reference regex is the backend's records.REFERENCE_RE", () => {
    const records = readFileSync(
      join(
        process.cwd(),
        "..",
        "..",
        "plugins",
        "ihr_platform",
        "ihr_access",
        "records.py",
      ),
      "utf-8",
    );
    const match = /REFERENCE_RE = re\.compile\(r"([^"]+)"\)/.exec(records);
    assert.ok(match, "REFERENCE_RE in records.py");
    assert.equal(REFERENCE_RE.source.replace(/\\\//g, "/"), match[1]);
  });

  it("requires a reference for legal and insurer only", () => {
    assert.equal(referenceRequired("legal"), true);
    assert.equal(referenceRequired("insurer"), true);
    assert.equal(referenceRequired("patient_request"), false);
    assert.equal(referenceRequired("nabh_review"), false);
  });

  it("an insurer request without a reference is refused with a field error and no request", () => {
    const result = validateRequest({
      patient: PATIENT,
      reason: "insurer",
      reference: "   ",
    });
    assert.equal(result.request, null);
    assert.equal(result.errors.reference, FORM_ERROR_KEYS.referenceRequired);
  });

  it("accepts case numbers and trims them", () => {
    const result = validateRequest({
      patient: PATIENT,
      reason: "insurer",
      reference: " TEST-1 ",
    });
    assert.deepEqual(result.errors, {});
    assert.deepEqual(result.request, {
      patient: PATIENT,
      reason: "insurer",
      reference: "TEST-1",
    });
    for (const ok of ["A1", "CLM/2026/17", "case no. 5", "x_y:z-1"]) {
      assert.equal(
        validateRequest({ patient: PATIENT, reason: "legal", reference: ok })
          .request?.reference,
        ok,
      );
    }
  });

  it("refuses a reference with free text characters or over 100 characters", () => {
    for (const bad of ["-start", "has;semicolon", "<b>", "a".repeat(101)]) {
      const result = validateRequest({
        patient: PATIENT,
        reason: "legal",
        reference: bad,
      });
      assert.equal(result.request, null, bad);
      assert.equal(result.errors.reference, FORM_ERROR_KEYS.referenceInvalid);
    }
    // a reference is optional for the other reasons, but when given it is checked too
    assert.equal(
      validateRequest({
        patient: PATIENT,
        reason: "patient_request",
        reference: "a;b",
      }).request,
      null,
    );
    assert.deepEqual(
      validateRequest({
        patient: PATIENT,
        reason: "patient_request",
        reference: "",
      }).errors,
      {},
    );
  });

  it("needs a patient and a known reason", () => {
    const result = validateRequest({
      patient: undefined,
      reason: "",
      reference: "",
    });
    assert.equal(result.request, null);
    assert.equal(result.errors.patient, FORM_ERROR_KEYS.patient);
    assert.equal(result.errors.reason, FORM_ERROR_KEYS.reason);
    assert.equal(
      validateRequest({ patient: PATIENT, reason: "fun", reference: "" }).errors
        .reason,
      FORM_ERROR_KEYS.reason,
    );
  });
});

describe("the patient of a grant (Q-K51 A, SS2/FF3)", () => {
  it("never takes an id from a partial: true search result", () => {
    assert.equal(
      patientIdForGrant({ id: "0b1f6a52", partial: true }),
      undefined,
    );
    assert.equal(patientIdForGrant({ id: PATIENT, partial: true }), undefined);
    assert.equal(patientIdForGrant(null), undefined);
    assert.equal(patientIdForGrant({}), undefined);
  });

  it("takes the id of a full patient body", () => {
    assert.equal(patientIdForGrant({ id: PATIENT }), PATIENT);
    assert.equal(patientIdForGrant({ id: PATIENT, partial: false }), PATIENT);
  });

  it("reads ?patient= only when it is a uuid", () => {
    assert.equal(patientFromQuery(PATIENT.toUpperCase()), PATIENT);
    assert.equal(patientFromQuery("abc"), undefined);
    assert.equal(patientFromQuery(undefined), undefined);
    assert.equal(patientFromQuery(["a"]), undefined);
    assert.equal(patientFromQuery(`${PATIENT}/x`), undefined);
  });
});

describe("server messages", () => {
  it("puts a 400 message beside the field it names", () => {
    assert.equal(
      serverErrorField("This reason needs a reference (case or claim number)."),
      "reference",
    );
    assert.equal(serverErrorField("Give a reason."), "reason");
    assert.equal(serverErrorField("No such patient."), "form");
    assert.equal(detailOf({ detail: "No such patient." }), "No such patient.");
    assert.equal(detailOf({}), undefined);
    assert.equal(detailOf(undefined), undefined);
  });
});

describe("countdown", () => {
  it("shows mm:ss and never goes below zero", () => {
    assert.equal(countdownText(1421), "23:41");
    assert.equal(countdownText(59), "00:59");
    assert.equal(countdownText(0), "00:00");
    assert.equal(countdownText(-5), "00:00");
    assert.equal(countdownText(Number.NaN), "00:00");
    assert.equal(countdownText(3909), "1:05:09");
  });

  it("counts down from seconds_left at the time of the fetch", () => {
    const g = grant(1, 100);
    assert.equal(secondsLeftAt(g, 1_000_000, 1_000_000), 100);
    assert.equal(secondsLeftAt(g, 1_000_000, 1_030_500), 70);
    assert.equal(secondsLeftAt(g, 1_000_000, 1_200_000), 0);
    // a clock that went backwards never adds time
    assert.equal(secondsLeftAt(g, 1_000_000, 900_000), 100);
  });

  it("finds the earliest expiry", () => {
    assert.equal(earliestExpiryMs([], 5), null);
    assert.equal(earliestExpiryMs([grant(1, 300), grant(2, 60)], 1000), 61_000);
  });

  it("takes the length of a grant from the backend (45 minutes)", () => {
    assert.equal(noticeMinutes(45), 45);
    assert.equal(noticeMinutes(30), 30);
    assert.equal(noticeMinutes(undefined), null);
    assert.equal(noticeMinutes(0), null);
    assert.equal(noticeMinutes(Number.NaN), null);
  });

  it("the notice text of our strings carries {{minutes}} and no fixed length", () => {
    const en = JSON.parse(
      readFileSync(
        join(process.cwd(), "..", "..", "deploy", "locale", "en.json"),
        "utf-8",
      ),
    ) as Record<string, string>;
    const text = en["ihr_records_notice"];
    assert.ok(text.includes("{{minutes}}"));
    assert.ok(!/\b30\b/.test(text));
    // fed 45, as i18next does
    assert.ok(
      text.replace("{{minutes}}", String(noticeMinutes(45))).includes("45"),
    );
  });

  it("formats the start in India time, DD-MM-YYYY HH:mm", () => {
    // 08:30 UTC is 14:00 IST
    assert.equal(
      formatStarted("2026-11-16T08:30:00+00:00"),
      "16-11-2026 14:00",
    );
    // 20:00 UTC on the 15th is 01:30 IST on the 16th
    assert.equal(formatStarted("2026-11-15T20:00:00Z"), "16-11-2026 01:30");
    assert.equal(formatStarted("not a date"), "-");
  });
});

describe("the grant watcher's rules (SS1, fail closed)", () => {
  const at = 1_000_000;

  it("a first list holds the grants and does nothing", () => {
    const step = watchStep(WATCH_START, {
      type: "listed",
      grants: [grant(1, 600)],
      atMs: at,
    });
    assert.equal(step.action, "none");
    assert.deepEqual(step.state.held, [
      { id: 1, patient: PATIENT, expiresAtMs: at + 600_000 },
    ]);
  });

  it("a held grant that disappears from the list is ended", () => {
    const held = watchStep(WATCH_START, {
      type: "listed",
      grants: [grant(1, 600), grant(2, 600)],
      atMs: at,
    }).state;
    const same = watchStep(held, {
      type: "listed",
      grants: [grant(1, 570), grant(2, 570)],
      atMs: at + 30_000,
    });
    assert.equal(same.action, "none");
    const lost = watchStep(same.state, {
      type: "listed",
      grants: [grant(2, 540)],
      atMs: at + 60_000,
    });
    assert.equal(lost.action, "ended");
    assert.deepEqual(
      lost.state.held.map((g) => g.id),
      [2],
    );
  });

  it("a new grant is not an end, and no grants held never ends anything", () => {
    assert.equal(
      watchStep(WATCH_START, { type: "listed", grants: [], atMs: at }).action,
      "none",
    );
    const held = watchStep(WATCH_START, {
      type: "listed",
      grants: [grant(1, 600)],
      atMs: at,
    }).state;
    assert.equal(
      watchStep(held, {
        type: "listed",
        grants: [grant(1, 590), grant(3, 900)],
        atMs: at + 10_000,
      }).action,
      "none",
    );
  });

  it("a grant at zero seconds left counts as gone", () => {
    const held = watchStep(WATCH_START, {
      type: "listed",
      grants: [grant(1, 600)],
      atMs: at,
    }).state;
    assert.equal(
      watchStep(held, {
        type: "listed",
        grants: [grant(1, 0)],
        atMs: at + 600_000,
      }).action,
      "ended",
    );
  });

  it("the timer at the earliest expiry ends the grant without a reply", () => {
    const held = watchStep(WATCH_START, {
      type: "listed",
      grants: [grant(1, 60), grant(2, 600)],
      atMs: at,
    }).state;
    assert.equal(
      watchStep(held, { type: "tick", atMs: at + 59_000 }).action,
      "none",
    );
    const done = watchStep(held, { type: "tick", atMs: at + 60_000 });
    assert.equal(done.action, "ended");
    assert.deepEqual(
      done.state.held.map((g) => g.id),
      [2],
    );
  });

  it("fails closed: two failures in a row while a grant is held", () => {
    assert.equal(MAX_FAILURES_WHILE_HELD, 2);
    const held = watchStep(WATCH_START, {
      type: "listed",
      grants: [grant(1, 600)],
      atMs: at,
    }).state;
    const first = watchStep(held, { type: "failed" });
    assert.equal(first.action, "none");
    assert.equal(first.state.failures, 1);
    const second = watchStep(first.state, { type: "failed" });
    assert.equal(second.action, "unconfirmed");
    assert.deepEqual(second.state.held, []);
    // the grant is listed again once the server answers: not an end, the clerk reopens
    const back = watchStep(second.state, {
      type: "listed",
      grants: [grant(1, 500)],
      atMs: at + 100_000,
    });
    assert.equal(back.action, "none");
  });

  it("a success between two failures resets the count", () => {
    const held = watchStep(WATCH_START, {
      type: "listed",
      grants: [grant(1, 600)],
      atMs: at,
    }).state;
    const one = watchStep(held, { type: "failed" }).state;
    const ok = watchStep(one, {
      type: "listed",
      grants: [grant(1, 560)],
      atMs: at + 40_000,
    });
    assert.equal(ok.state.failures, 0);
    assert.equal(watchStep(ok.state, { type: "failed" }).action, "none");
  });

  it("failures with nothing held do nothing", () => {
    let state = WATCH_START;
    for (let i = 0; i < 5; i++) {
      const step = watchStep(state, { type: "failed" });
      assert.equal(step.action, "none");
      state = step.state;
    }
  });

  it("finds the live grant of a patient", () => {
    const list = [grant(1, 100)];
    assert.equal(liveGrantFor(list, PATIENT)?.id, 1);
    assert.equal(liveGrantFor(list, "other"), undefined);
    assert.equal(liveGrantFor(undefined, PATIENT), undefined);
    assert.equal(liveGrantFor(list, undefined), undefined);
  });
});

function summary(
  id: number,
  secondsLeft: number,
  patient = PATIENT,
): GrantSummary {
  return {
    id,
    patient,
    expires_at: "2026-11-16T09:00:00+00:00",
    seconds_left: secondsLeft,
  };
}

describe("the watcher never trusts a grant it has not confirmed (review F2)", () => {
  const at = 2_000_000;
  const OTHER = "11111111-2222-4333-8444-555555555555";

  it("a grant noted from the POST answer is held before any list", () => {
    const noted = watchStep(WATCH_START, {
      type: "noted",
      grant: summary(7, 1800),
      atMs: at,
    });
    assert.equal(noted.action, "none");
    assert.deepEqual(
      noted.state.held.map((g) => [g.id, g.patient, g.expiresAtMs]),
      [[7, PATIENT, at + 1_800_000]],
    );
    // its first looks fail: fail closed, with no list ever had
    const one = watchStep(noted.state, { type: "failed" });
    assert.equal(one.action, "none");
    assert.equal(
      watchStep(one.state, { type: "failed" }).action,
      "unconfirmed",
    );
    // it runs out at its time without any answer
    assert.equal(
      watchStep(noted.state, { type: "tick", atMs: at + 1_800_000 }).action,
      "ended",
    );
  });

  it("a list sent before the grant was noted keeps it; one sent after drops it (ended)", () => {
    const noted = watchStep(WATCH_START, {
      type: "noted",
      grant: summary(7, 1800),
      atMs: at,
    }).state;
    const stale = watchStep(noted, {
      type: "listed",
      grants: [],
      atMs: at + 200,
      sentAtMs: at - 300,
      openCharts: [PATIENT],
    });
    assert.equal(stale.action, "none");
    assert.deepEqual(
      stale.state.held.map((g) => g.id),
      [7],
    );
    const fresh = watchStep(stale.state, {
      type: "listed",
      grants: [],
      atMs: at + 5_000,
      sentAtMs: at + 4_000,
    });
    assert.equal(fresh.action, "ended");
    assert.deepEqual(fresh.state.held, []);
    // listed by a later GET: an ordinary held grant from then on
    const confirmed = watchStep(stale.state, {
      type: "listed",
      grants: [summary(7, 1790)],
      atMs: at + 10_000,
      sentAtMs: at + 9_000,
    });
    assert.equal(confirmed.action, "none");
    assert.equal(confirmed.state.held[0].notedAtMs, undefined);
  });

  it("an open chart with nothing held: two failed looks are unconfirmed", () => {
    const one = watchStep(WATCH_START, {
      type: "failed",
      openCharts: [PATIENT],
    });
    assert.equal(one.action, "none");
    const two = watchStep(one.state, { type: "failed", openCharts: [PATIENT] });
    assert.equal(two.action, "unconfirmed");
  });

  it("a refusal before any list, on an open chart, counts once like a failed look", () => {
    const refused = watchStep(WATCH_START, {
      type: "refused",
      openCharts: [PATIENT],
    });
    assert.equal(refused.action, "none");
    // more refusals before an answer do not add up (a chart load may meet several)
    const again = watchStep(refused.state, {
      type: "refused",
      openCharts: [PATIENT],
    });
    assert.equal(again.action, "none");
    assert.equal(again.state.failures, 1);
    // the look it asks for fails too: unconfirmed
    assert.equal(
      watchStep(again.state, { type: "failed", openCharts: [PATIENT] }).action,
      "unconfirmed",
    );
    // a refusal with no chart open and nothing held never acts
    let state = WATCH_START;
    for (let i = 0; i < 3; i++) {
      const step = watchStep(state, { type: "refused" });
      assert.equal(step.action, "none");
      state = watchStep(step.state, { type: "failed" }).state;
    }
  });

  it("a refusal after a list leaves the decision to the look it asks for", () => {
    const listed = watchStep(WATCH_START, {
      type: "listed",
      grants: [summary(1, 600)],
      atMs: at,
    }).state;
    const refused = watchStep(listed, {
      type: "refused",
      openCharts: [PATIENT],
    });
    assert.equal(refused.action, "none");
    assert.equal(refused.state.failures, 0);
  });

  it("a chart that no listed grant covers is ended (the grant ended before the first list)", () => {
    const first = watchStep(WATCH_START, {
      type: "listed",
      grants: [summary(2, 600, OTHER)],
      atMs: at,
      openCharts: [PATIENT],
    });
    assert.equal(first.action, "ended");
    assert.equal(
      watchStep(WATCH_START, {
        type: "listed",
        grants: [summary(1, 600)],
        atMs: at,
        openCharts: [PATIENT],
      }).action,
      "none",
    );
  });
});

describe("looks: one at a time, none dropped, none hanging (reviews F3, F4, TS4)", () => {
  function deferred() {
    let resolve!: () => void;
    const promise = new Promise<void>((r) => (resolve = r));
    return { promise, resolve };
  }

  it("a look asked for during another is sent after it, and the caller waits for it", async () => {
    const started: number[] = [];
    const gates = [deferred(), deferred()];
    let n = 0;
    const refetch = serialLooks(() => {
      const i = n++;
      started.push(i);
      return gates[i].promise;
    });
    const first = refetch();
    // End now's DELETE answered while the first look runs: its look must not be dropped
    let secondDone = false;
    const second = refetch().then(() => void (secondDone = true));
    const third = refetch();
    assert.deepEqual(started, [0]);
    gates[0].resolve();
    await first;
    await new Promise((r) => setTimeout(r, 0));
    assert.deepEqual(started, [0, 1], "one more look, sent after the first");
    assert.equal(secondDone, false, "the caller waits for the later look");
    gates[1].resolve();
    await Promise.all([second, third]);
    assert.equal(secondDone, true);
    assert.equal(n, 2, "callers during one look share the one later look");
  });

  it("a failing look never blocks the next", async () => {
    let n = 0;
    const refetch = serialLooks(async () => {
      n++;
      throw new Error("down");
    });
    await refetch();
    await refetch();
    assert.equal(n, 2);
  });

  it(
    "a GET that never answers fails after the limit",
    { timeout: 2_000 },
    async () => {
      assert.equal(LOOK_TIMEOUT_MS, 10_000);
      let aborted = false;
      const hung = (signal: AbortSignal) =>
        new Promise<string>(() => {
          signal.addEventListener("abort", () => void (aborted = true));
        });
      await assert.rejects(timedLook(hung, 20));
      assert.equal(aborted, true, "the request itself is aborted");
      // and a serial look built on it frees the next look
      let looks = 0;
      const refetch = serialLooks(async () => {
        looks++;
        await timedLook(hung, 20).catch(() => undefined);
      });
      await refetch();
      await refetch();
      assert.equal(looks, 2);
    },
  );

  it(
    "an answer in time is returned; unmounting aborts a look at once",
    { timeout: 2_000 },
    async () => {
      assert.equal(await timedLook(async () => "ok", 1_000), "ok");
      const outer = new AbortController();
      const pending = timedLook(
        (signal) =>
          new Promise<string>((_, reject) =>
            signal.addEventListener("abort", () =>
              reject(new Error("aborted")),
            ),
          ),
        60_000,
        outer.signal,
      );
      outer.abort();
      await assert.rejects(pending);
    },
  );
});

describe("the records page's details (review BA1)", () => {
  it("rows follow the watcher's live list; details only from the ?names=1 answer", () => {
    const live = [summary(3, 100), summary(1, 200)];
    const rows = withDetails(live, [grant(1, 200), grant(9, 10)]);
    assert.deepEqual(
      rows.map((r) => [r.grant.id, r.details?.patient_name ?? null]),
      [
        [3, null],
        [1, "Asha Rao"],
      ],
    );
    assert.deepEqual(
      withDetails(live, undefined).map((r) => r.details),
      [null, null],
    );
  });

  it("one list with names per set of live grants, whatever their order", () => {
    assert.equal(detailsKey([summary(3, 1), summary(1, 1)]), "1,3");
    assert.equal(detailsKey([]), "");
  });
});
