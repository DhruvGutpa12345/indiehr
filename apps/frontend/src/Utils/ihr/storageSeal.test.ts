import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, it } from "node:test";

import {
  fillDraftStorageKeys,
  writeFillDraftCache,
} from "@/components/QuestionnaireV2/fill/draft/fillDraftCache";
import {
  sealStaffStorage,
  staffStorageSealed,
  unsealForTest,
} from "./storageSeal";

// A minimal in-memory localStorage for node (the draft cache only needs these calls)
class MemoryStorage {
  private items = new Map<string, string>();
  get length() {
    return this.items.size;
  }
  key(i: number) {
    return [...this.items.keys()][i] ?? null;
  }
  getItem(k: string) {
    return this.items.has(k) ? (this.items.get(k) as string) : null;
  }
  setItem(k: string, v: string) {
    this.items.set(k, String(v));
  }
  removeItem(k: string) {
    this.items.delete(k);
  }
  clear() {
    this.items.clear();
  }
}
const memory = new MemoryStorage();
Object.defineProperty(globalThis, "localStorage", {
  value: new Proxy(memory, {
    ownKeys: () =>
      Array.from({ length: memory.length }, (_, i) => memory.key(i) as string),
    getOwnPropertyDescriptor: (_t, k) =>
      typeof k === "string" && memory.getItem(k) !== null
        ? { enumerable: true, configurable: true, value: memory.getItem(k) }
        : undefined,
  }),
  configurable: true,
});

const scope = {
  userId: "u1",
  subjectKey: "patient-1",
  entryQuestionnaireId: "q1",
};

describe("no patient draft is written after a session end (P2-T0b review F1)", () => {
  beforeEach(() => {
    memory.clear();
    unsealForTest();
  });

  it("writes a draft while the session lasts", () => {
    assert.equal(staffStorageSealed(), false);
    assert.equal(writeFillDraftCache(scope, '{"a":1}'), true);
    assert.equal(fillDraftStorageKeys().length, 1);
  });

  it("refuses a draft written after the storage was cleared (pagehide, unmount flush)", () => {
    sealStaffStorage();
    assert.equal(staffStorageSealed(), true);
    assert.equal(writeFillDraftCache(scope, '{"a":1}'), false);
    assert.deepEqual(fillDraftStorageKeys(), []);
  });
});

describe("every session end seals the storage (P2-T0b verifier)", () => {
  it("clearStaffStorage() calls sealStaffStorage() before its sweep", () => {
    const src = readFileSync(
      join(process.cwd(), "src", "Utils", "ihr", "staffSession.ts"),
      "utf-8",
    );
    const body = src.slice(src.indexOf("export function clearStaffStorage()"));
    const seal = body.indexOf("sealStaffStorage();");
    assert.ok(seal > 0, "clearStaffStorage seals");
    assert.ok(seal < body.indexOf("removeItem"), "before the sweep");
  });
});
