import { describe, expect, it } from "vitest";
import { axisWidth, formatClock, layoutSchedule, parseClock, PX_PER_HOUR } from "./schedule-layout";

const base = { start: "09:00", end: "13:00" };

describe("parseClock", () => {
  it("reads HH:MM and refuses anything else", () => {
    expect(parseClock("09:30")).toBe(570);
    expect(parseClock("23:59")).toBe(1439);
    expect(parseClock("24:00")).toBeNull();
    expect(parseClock("9:60")).toBeNull();
    expect(parseClock("nine")).toBeNull();
  });
  it("round-trips through formatClock", () => {
    expect(formatClock(parseClock("07:05")!)).toBe("07:05");
  });
});

describe("layoutSchedule", () => {
  it("places a block by minutes as a percent of the axis", () => {
    const l = layoutSchedule({ ...base, lanes: [{ name: "A", blocks: [{ from: "10:00", to: "11:00", title: "x" }] }] });
    const b = l.lanes[0].blocks[0];
    expect(l.span).toBe(240);
    expect(b.left).toBe(25);
    expect(b.width).toBe(25);
    expect(b.tone).toBe("neutral");
  });

  it("clamps a block that leaves the axis and drops one wholly outside", () => {
    const l = layoutSchedule({
      ...base,
      lanes: [
        {
          name: "A",
          blocks: [
            { from: "08:00", to: "09:30", title: "early" },
            { from: "12:30", to: "15:00", title: "late" },
            { from: "14:00", to: "15:00", title: "gone" },
            { from: "07:00", to: "08:00", title: "gone too" },
          ],
        },
      ],
    });
    expect(l.lanes[0].blocks.map((b) => [b.title, b.from, b.to])).toEqual([
      ["early", "09:00", "09:30"],
      ["late", "12:30", "13:00"],
    ]);
    for (const b of l.lanes[0].blocks) expect(b.left + b.width).toBeLessThanOrEqual(100);
  });

  it("orders blocks by time whatever order they arrive in", () => {
    const l = layoutSchedule({
      ...base,
      lanes: [{ name: "A", blocks: [{ from: "11:00", to: "12:00", title: "b" }, { from: "09:00", to: "10:00", title: "a" }] }],
    });
    expect(l.lanes[0].blocks.map((b) => b.title)).toEqual(["a", "b"]);
  });

  it("never throws on a reversed axis, a reversed block or junk", () => {
    const l = layoutSchedule({
      start: "18:00",
      end: "09:00",
      lanes: [{ name: "A", blocks: [{ from: "19:00", to: "18:30", title: "r" }, { from: "zz", to: "19:00", title: "j" }, { from: "18:10", to: "18:20", title: "ok" }] }],
    });
    expect(l.span).toBe(60);
    expect(l.lanes[0].blocks.map((b) => b.title)).toEqual(["ok"]);
  });

  it("keeps an empty lane as one empty row", () => {
    const l = layoutSchedule({ ...base, lanes: [{ name: "A", blocks: [] }] });
    expect(l.lanes[0]).toEqual({ name: "A", blocks: [], rows: 1 });
  });

  it("stacks a lane that overlaps itself instead of drawing over", () => {
    const l = layoutSchedule({
      ...base,
      lanes: [{ name: "A", blocks: [{ from: "09:00", to: "11:00", title: "a" }, { from: "10:00", to: "12:00", title: "b" }, { from: "11:00", to: "12:00", title: "c" }] }],
    });
    expect(l.lanes[0].blocks.map((b) => b.row)).toEqual([0, 1, 0]);
    expect(l.lanes[0].rows).toBe(2);
  });

  it("puts an hour label at each hour and none where it would not fit", () => {
    const l = layoutSchedule({ ...base, lanes: [{ name: "A", blocks: [] }] });
    expect(l.ticks.filter((t) => t.label).map((t) => t.label)).toEqual(["09:00", "10:00", "11:00", "12:00"]);
    expect(l.ticks.filter((t) => t.hour)).toHaveLength(5);
    expect(l.ticks).toHaveLength(9);
    expect(l.ticks[0].left).toBe(0);
    expect(l.ticks.at(-1)!.left).toBe(100);
  });

  it("starts ticks at the first half hour inside an off-grid axis", () => {
    const l = layoutSchedule({ start: "09:10", end: "11:00", lanes: [{ name: "A", blocks: [] }] });
    expect(l.ticks[0].min).toBe(9 * 60 + 30);
    expect(l.ticks[0].label).toBeNull();
  });

  it("makes seven hours wider than a phone", () => {
    expect(axisWidth(7 * 60)).toBe(7 * PX_PER_HOUR);
    expect(axisWidth(7 * 60)).toBeGreaterThan(390);
  });
});
