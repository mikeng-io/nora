import { describe, expect, it } from "vitest";
import en from "../../../messages/en.json";
import zh from "../../../messages/zh-Hant.json";
import { BOTTOM_NAV_COLUMNS, MAIN_NAV, MOBILE_NAV_MAX } from "./nav";

// The phone's bottom bar: which places it holds, and that it still fits them.
describe("the phone's bottom bar", () => {
  const mobile = MAIN_NAV.filter((i) => i.mobile);

  it("holds Documents", () => {
    expect(mobile.map((i) => i.key)).toContain("documents");
  });

  it("holds no more than it fits, and has a column class for every count it can hold", () => {
    expect(mobile.length).toBeLessThanOrEqual(MOBILE_NAV_MAX);
    for (let n = 1; n <= MOBILE_NAV_MAX; n++) {
      expect(BOTTOM_NAV_COLUMNS[n], `${n} items`).toBe(`grid-cols-${n}`);
    }
    // The bar picks its columns by this count: a bar of N items without an N-column class
    // would render as one stacked column.
    expect(BOTTOM_NAV_COLUMNS[mobile.length]).toBeDefined();
  });

  it("labels each place short enough for a 320px phone, in both languages", () => {
    // A 320px bar over five places gives each 64px; the label is 11px semibold, so about
    // 6px a Latin letter and 11px a Chinese character.
    for (const item of mobile) {
      const english = (en.nav as Record<string, string>)[item.key];
      const chinese = (zh.nav as Record<string, string>)[item.key];
      expect(english.length, `en ${item.key}`).toBeLessThanOrEqual(10);
      expect(chinese.length, `zh ${item.key}`).toBeLessThanOrEqual(5);
    }
  });
});
