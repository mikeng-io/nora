import { describe, expect, it } from "vitest";
import { corridorEndpoints, relativeAge } from "./presence-format";

describe("corridorEndpoints", () => {
  it("names a corridor from its sequence ends", () => {
    expect(
      corridorEndpoints({ sequence: ["Nathan Road", "Argyle Street", "Harbour City"] }),
    ).toEqual({ from: "Nathan Road", to: "Harbour City" });
  });

  it("keeps only the first comma segment of a full Life360 address", () => {
    expect(
      corridorEndpoints({
        sequence: ["Nathan Road, Tsim Sha Tsui, Hong Kong", "Harbour City, Tsim Sha Tsui, Hong Kong"],
      }),
    ).toEqual({ from: "Nathan Road", to: "Harbour City" });
  });

  it("returns null for an address-less sequence so the caller falls back to the id", () => {
    expect(corridorEndpoints({ sequence: [] })).toBeNull();
  });

  it("round-trips read from === to (a reversed sequence is a different corridor)", () => {
    expect(corridorEndpoints({ sequence: ["Home", "School", "Home"] })).toEqual({
      from: "Home",
      to: "Home",
    });
  });
});

describe("relativeAge", () => {
  it("ladders seconds into the compact units the rows show", () => {
    expect(relativeAge(12)).toBe("now");
    expect(relativeAge(300)).toBe("5m");
    expect(relativeAge(7_200)).toBe("2h");
    expect(relativeAge(172_800)).toBe("2d");
  });
});
