import { describe, expect, it } from "vitest";
import type { Fix } from "./api/presence";
import { haversineM, namePlace, presenceKind, sortPresence } from "./presence";

const fix = (over: Partial<Fix>): Fix => ({
  lat: 22.3,
  lng: 114.17,
  ts: 0,
  ageSeconds: 5,
  wireState: "live",
  ...over,
});

describe("presenceKind", () => {
  it("splits a driving flag by how fresh the fix is", () => {
    expect(presenceKind({ current: fix({ isDriving: true, ageSeconds: 10 }) })).toBe("driving");
    expect(presenceKind({ current: fix({ isDriving: true, ageSeconds: 600 }) })).toBe("driving-stale");
    expect(presenceKind({ current: fix({}) })).toBe("live");
  });

  it("keeps 'could not check' apart from 'no position'", () => {
    expect(presenceKind({ current: fix({ wireState: "unavailable" }) })).toBe("unavailable");
    expect(presenceKind({ current: null })).toBe("none");
    expect(presenceKind({ current: fix({ wireState: "sharing_off" }) })).toBe("sharing_off");
  });
});

describe("sortPresence", () => {
  it("puts the moving and the present first, the unknown last", () => {
    const e = (name: string, current: Fix | null) => ({ personId: name, displayName: name, current });
    const sorted = sortPresence([
      e("none", null),
      e("stale", fix({ wireState: "stale", ageSeconds: 7200 })),
      e("here", fix({})),
      e("driving", fix({ isDriving: true })),
    ]);
    expect(sorted.map((s) => s.displayName)).toEqual(["driving", "here", "stale", "none"]);
  });
});

describe("namePlace", () => {
  const saved = [{ name: "Home", lat: 22.3075, lng: 114.2596 }];
  const derived = [
    {
      kind: "work",
      label: null,
      lat: 22.3128,
      lng: 114.2252,
      radiusM: 120,
      confidence: 0.8,
      firstSeenTs: 0,
      lastSeenTs: 0,
      stayCount: 3,
      totalDwellMs: 1,
    },
  ];

  it("names a saved place within its radius", () => {
    expect(namePlace({ lat: 22.3076, lng: 114.2597 }, saved, derived)).toEqual({ source: "saved", name: "Home" });
  });

  it("falls back to a confident derived place, never while driving", () => {
    expect(namePlace({ lat: 22.3129, lng: 114.2251 }, saved, derived)).toEqual({
      source: "derived",
      kind: "work",
      label: null,
    });
    expect(namePlace({ lat: 22.3076, lng: 114.2597, isDriving: true }, saved, derived)).toBeNull();
    expect(namePlace({ lat: 22.2, lng: 114.1 }, saved, derived)).toBeNull();
  });

  it("measures distance on the sphere", () => {
    expect(Math.round(haversineM({ lat: 22.3, lng: 114.17 }, { lat: 22.31, lng: 114.17 }))).toBe(1112);
  });
});
