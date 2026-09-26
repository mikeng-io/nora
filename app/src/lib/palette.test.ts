import { describe, expect, it } from "vitest";
import { paletteScore } from "./palette";

describe("paletteScore", () => {
  it("ranks a person's name above a tool that mentions the same letters", () => {
    const person = paletteScore("person Ken", "ken", ["ken"]);
    const tool = paletteScore("tool memory_search", "ken", ["Looks up what was spoken in a room"]);
    expect(person).toBeGreaterThan(tool);
    expect(tool).toBeGreaterThan(0);
  });

  it("prefers a prefix to a match in the middle, and drops a non-match", () => {
    expect(paletteScore("server Abiotic Factor", "abi")).toBeGreaterThan(paletteScore("plan Kabi dinner", "abi"));
    expect(paletteScore("page Map", "zzz")).toBe(0);
    expect(paletteScore("page Map", "")).toBe(1);
  });
});
