import { describe, expect, it } from "vitest";
import { PERSON_HUES, hueOf, initials, oklchToHex, personColorFor } from "./person";

// The rule the hue set exists for: no person reads as Nora or as a state.
// Reserved OKLCH hues, measured from DESIGN.md's tokens in both lightings:
// danger 17-21, blush 30-31, caution 78-80, success 166, denim 265-266.
const RESERVED = [17, 21, 30, 31, 78, 80, 166, 265, 266];
const distance = (a: number, b: number) => Math.min(Math.abs(a - b), 360 - Math.abs(a - b));

describe("person colours", () => {
  it("stay at least 25° from every reserved hue", () => {
    for (const h of PERSON_HUES) {
      for (const r of RESERVED) expect(distance(h, r), `${h} vs ${r}`).toBeGreaterThanOrEqual(25);
    }
  });

  it("are stable per id and drawn from the set", () => {
    expect(hueOf("miles")).toBe(hueOf("miles"));
    expect(PERSON_HUES).toContain(hueOf("someone-else"));
    expect(personColorFor("miles", "light")).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("convert OKLCH to the same sRGB a browser would", () => {
    // oklch(0.628 0.2577 29.23) is sRGB red.
    expect(oklchToHex(0.62796, 0.25768, 29.2339)).toBe("#ff0000");
  });
});

describe("initials", () => {
  it("takes two Latin initials, one CJK character", () => {
    expect(initials("Miles Novak")).toBe("MN");
    expect(initials("ken")).toBe("K");
    expect(initials("陳大文")).toBe("陳");
    expect(initials("  ")).toBe("?");
  });
});
