import { describe, expect, it } from "vitest";
import { PresenceEntrySchema, PresenceHistorySchema } from "./presence-schema";

describe("PresenceEntrySchema", () => {
  it("parses an entry with a current fix", () => {
    const body = {
      personId: "miles",
      displayName: "Miles",
      current: {
        lat: 22.3, lng: 114.2, ts: 1_755_000_000_000, ageSeconds: 30,
        wireState: "live", batteryPercent: 80, isDriving: false,
      },
    };
    expect(() => PresenceEntrySchema.parse(body)).not.toThrow();
  });

  it("allows current: null for a member with no recorded fix", () => {
    const body = { personId: "ada", displayName: "Ada", current: null };
    expect(() => PresenceEntrySchema.parse(body)).not.toThrow();
  });

  it("rejects an unknown wireState value", () => {
    const body = {
      personId: "miles", displayName: "Miles",
      current: { lat: 0, lng: 0, ts: 0, ageSeconds: 0, wireState: "bogus" },
    };
    expect(() => PresenceEntrySchema.parse(body)).toThrow();
  });
});

describe("PresenceHistorySchema", () => {
  it("parses a real history response", () => {
    const body = {
      hours: 24,
      from: 1_754_913_600_000,
      to: 1_755_000_000_000,
      points: [{ lat: 22.3, lng: 114.2, ts: 1_754_913_600_000 }],
    };
    expect(() => PresenceHistorySchema.parse(body)).not.toThrow();
  });

  it("parses an empty points array before anyone has any history", () => {
    const body = { hours: 24, from: 0, to: 0, points: [] };
    expect(() => PresenceHistorySchema.parse(body)).not.toThrow();
  });
});
