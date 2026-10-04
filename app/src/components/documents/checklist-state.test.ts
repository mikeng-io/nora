import { describe, expect, it } from "vitest";
import { checklistKey, deviceStorage, hashText, readTicks, writeTicks } from "./checklist-state";

const mem = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k), m };
};
const throwing = {
  getItem: () => { throw new Error("blocked"); },
  setItem: () => { throw new Error("quota"); },
  removeItem: () => { throw new Error("blocked"); },
};

describe("hashText", () => {
  it("is stable and short", () => {
    expect(hashText("abc")).toBe(hashText("abc"));
    expect(hashText("abc")).not.toBe(hashText("abd"));
    expect(hashText("")).toMatch(/^[0-9a-z]+$/);
  });
});

describe("checklistKey", () => {
  it("names the document, or none for a fixture", () => {
    expect(checklistKey("d1", undefined, ["a"])).toMatch(/^nora:check:d1:[0-9a-z]+$/);
    expect(checklistKey(null, undefined, ["a"])).toMatch(/^nora:check::[0-9a-z]+$/);
  });
  it("changes with the items, so a revision starts clean", () => {
    expect(checklistKey("d", undefined, ["a", "b"])).not.toBe(checklistKey("d", undefined, ["a", "c"]));
  });
  it("does not collide on equal text under different titles", () => {
    expect(checklistKey("d", "Before", ["a"])).not.toBe(checklistKey("d", "After", ["a"]));
  });
  it("does not blur item boundaries", () => {
    expect(checklistKey("d", undefined, ["ab", "c"])).not.toBe(checklistKey("d", undefined, ["a", "bc"]));
  });
});

describe("readTicks / writeTicks", () => {
  it("round-trips, sorted and de-duplicated", () => {
    const s = mem();
    expect(writeTicks(s, "k", [2, 0])).toBe(true);
    expect(readTicks(s, "k", 3)).toEqual([0, 2]);
  });
  it("forgets the key when nothing is ticked", () => {
    const s = mem();
    writeTicks(s, "k", [1]);
    writeTicks(s, "k", []);
    expect(s.m.has("k")).toBe(false);
  });
  it("drops garbage and out-of-range entries", () => {
    const s = mem();
    s.setItem("k", JSON.stringify([0, 0, 1.5, -1, 9, "x", null, 2]));
    expect(readTicks(s, "k", 3)).toEqual([0, 2]);
    s.setItem("k", "{not json");
    expect(readTicks(s, "k", 3)).toEqual([]);
    s.setItem("k", '{"a":1}');
    expect(readTicks(s, "k", 3)).toEqual([]);
  });
  it("survives a storage that throws, or none at all", () => {
    expect(readTicks(throwing, "k", 3)).toEqual([]);
    expect(writeTicks(throwing, "k", [1])).toBe(false);
    expect(writeTicks(throwing, "k", [])).toBe(false);
    expect(readTicks(null, "k", 3)).toEqual([]);
    expect(writeTicks(null, "k", [1])).toBe(false);
  });
  it("deviceStorage is null outside a browser", () => {
    expect(deviceStorage()).toBeNull();
  });
});
