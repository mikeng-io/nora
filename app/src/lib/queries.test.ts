import { describe, expect, it } from "vitest";
import { keepFor, qk } from "./queries";

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
