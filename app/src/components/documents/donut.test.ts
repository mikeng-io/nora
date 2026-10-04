import { describe, expect, it } from "vitest";
import { donutSlices, formatPct } from "./donut";

describe("donutSlices", () => {
  it("shares the whole and chains the arcs", () => {
    const { total, slices } = donutSlices([{ label: "a", value: 1 }, { label: "b", value: 3 }]);
    expect(total).toBe(4);
    expect(slices.map((s) => s.pct)).toEqual([25, 75]);
    expect(slices[1].start).toBe(25);
  });
  it("ignores non-positive and non-finite values, and keeps colours by place", () => {
    const { slices } = donutSlices([{ label: "a", value: 0 }, { label: "b", value: -2 }, { label: "c", value: NaN }, { label: "d", value: 5 }]);
    expect(slices).toHaveLength(1);
    expect(slices[0].color).toBe("success");
  });
  it("never divides by zero", () => {
    expect(donutSlices([{ label: "a", value: 0 }, { label: "b", value: 0 }])).toEqual({ total: 0, slices: [] });
  });
  it("formats slivers", () => {
    expect(formatPct(0.2)).toBe("<1%");
    expect(formatPct(33.4)).toBe("33%");
  });
});
