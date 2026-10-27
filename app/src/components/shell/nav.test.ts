import { describe, expect, it } from "vitest";
import en from "../../../messages/en.json";
import zh from "../../../messages/zh-Hant.json";
import { BOTTOM_NAV_COLUMNS, MAIN_NAV, MOBILE_NAV_MAX } from "./nav";

// The phone's bottom bar: which places it holds, and that it still fits them.
// Its last cell is always More, so the places it holds are one short of the max.
describe("the phone's bottom bar", () => {
  const mobile = MAIN_NAV.filter((i) => i.mobile);

  it("holds Documents", () => {
    expect(mobile.map((i) => i.key)).toContain("documents");
  });

  it("holds no more than fits beside More, and has a column class for every count it can hold", () => {
    expect(mobile.length).toBeLessThanOrEqual(MOBILE_NAV_MAX - 1);
    for (let n = 1; n <= MOBILE_NAV_MAX; n++) {
      expect(BOTTOM_NAV_COLUMNS[n], `${n} cells`).toBe(`grid-cols-${n}`);
    }
    // The bar picks its columns by the item count plus the More cell: a bar of
    // N cells without an N-column class would render as one stacked column.
    expect(BOTTOM_NAV_COLUMNS[mobile.length + 1]).toBeDefined();
  });

  it("labels each place short enough for a 320px phone, in both languages", () => {
    // A 320px bar over five cells gives each 64px; the label is 11px semibold, so about
    // 6px a Latin letter and 11px a Chinese character.
    for (const key of [...mobile.map((i) => i.key), "more"]) {
      const english = (en.nav as Record<string, string>)[key];
      const chinese = (zh.nav as Record<string, string>)[key];
      expect(english.length, `en ${key}`).toBeLessThanOrEqual(10);
      expect(chinese.length, `zh ${key}`).toBeLessThanOrEqual(5);
    }
  });
});
