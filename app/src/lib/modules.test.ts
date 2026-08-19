import { describe, expect, it } from "vitest";
import { MODULES } from "./modules";

describe("MODULES", () => {
  it("lists status and memory as live, with unique ids", () => {
    const ids = MODULES.map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
    const live = MODULES.filter((m) => m.status === "live").map((m) => m.id);
    expect(live).toEqual(["status", "memory", "games", "map", "people", "events", "doctrine"]);
  });

  it("every module has a leading-slash href", () => {
    for (const m of MODULES) {
      expect(m.href.startsWith("/")).toBe(true);
    }
  });
});
