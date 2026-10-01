import { describe, expect, it } from "vitest";
import { keepFor, qk } from "./queries";

describe("documents query keys", () => {
  it("are distinct per page, filter and search, so one list is never served as another", () => {
    const keys = [
      qk.documents(1, "active", ""),
      qk.documents(2, "active", ""),
      qk.documents(1, "archived", ""),
      qk.documents(1, "active", "lisbon"),
      qk.documents(1, "archived", "lisbon"),
    ].map((k) => JSON.stringify(k));
    expect(new Set(keys).size).toBe(keys.length);
    // Every list shares the prefix a state change invalidates.
    expect(qk.documents(3, "archived", "x")[0]).toBe("documents");
  });
});

describe("keepFor", () => {
  it("keeps the last answer for another window of the same person", () => {
    const keep = keepFor("ken");
    expect(keep("ken's 6h route", { queryKey: qk.history("ken", 6) })).toBe("ken's 6h route");
  });

  it("never shows one person's answer while another's loads", () => {
    const keep = keepFor("miles");
    expect(keep("ken's route", { queryKey: qk.history("ken", 24) })).toBeUndefined();
    expect(keepFor("abiotic")("palworld players", { queryKey: qk.players("palworld", 1) })).toBeUndefined();
  });
});
