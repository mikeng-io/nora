import { describe, expect, it } from "vitest";
import {
  PresenceAnalyticsPlacesSchema,
  PresenceEntrySchema,
  PresenceHistorySchema,
  PresenceRhythmSchema,
  PresenceRoutesSchema,
} from "./presence-schema";

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

describe("PresenceAnalyticsPlacesSchema", () => {
  it("parses places grouped by subject", () => {
    expect(() =>
      PresenceAnalyticsPlacesSchema.parse({
        subjects: [
          {
            subjectId: "miles",
            places: [
              {
                kind: "home",
                label: "Tin Sgak Path",
                lat: 22.410,
                lng: 114.1245,
                radiusM: 120,
                confidence: 0.45,
                firstSeenTs: 1_787_133_855_000,
                lastSeenTs: 1_788_015_839_599,
                stayCount: 42,
                totalDwellMs: 1_782_000_000,
              },
            ],
          },
        ],
      }),
    ).not.toThrow();
  });

  it("rejects a place with a missing aggregate", () => {
    expect(() =>
      PresenceAnalyticsPlacesSchema.parse({
        subjects: [{ subjectId: "miles", places: [{ kind: "home", label: null }] }],
      }),
    ).toThrow();
  });
});

describe("PresenceRhythmSchema", () => {
  it("parses hour-of-week bins", () => {
    expect(() =>
      PresenceRhythmSchema.parse({
        subjectId: "miles",
        bins: [{ hourOfWeek: 0, stayCount: 3, totalDwellMs: 7_200_000 }],
        windowDays: 30,
      }),
    ).not.toThrow();
  });

  it("rejects an out-of-shape bin", () => {
    expect(() =>
      PresenceRhythmSchema.parse({
        subjectId: "miles",
        bins: [{ hourOfWeek: 0, stayCount: "3", totalDwellMs: 7_200_000 }],
        windowDays: 30,
      }),
    ).toThrow();
  });
});

describe("PresenceRoutesSchema", () => {
  it("parses trips and corridor metadata", () => {
    expect(() =>
      PresenceRoutesSchema.parse({
        subjectId: "miles",
        trips: [
          {
            id: 293,
            startedTs: 1_788_012_780_000,
            endedTs: 1_788_013_740_000,
            distanceM: 23_300,
            avgSpeedKmh: 87.2,
            fixCount: 41,
            corridorId: 2,
            corridorSampleCount: 5,
          },
        ],
        corridors: [
          {
            id: 2,
            sampleCount: 5,
            firstSeenTs: 1_787_900_000_000,
            lastSeenTs: 1_788_013_740_000,
            sequence: ["Nathan Road", "Argyle Street", "Harbour City"],
          },
        ],
      }),
    ).not.toThrow();
  });

  it("rejects a trip with a non-nullable identifier missing", () => {
    expect(() =>
      PresenceRoutesSchema.parse({ subjectId: "miles", trips: [{ corridorId: null }], corridors: [] }),
    ).toThrow();
  });
});
