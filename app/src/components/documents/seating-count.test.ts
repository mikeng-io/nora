import { describe, expect, it } from "vitest";
import { countSeating, seatPositions, tableFill } from "./seating-count";

const seat = (status?: "confirmed" | "pending" | "declined") => ({ name: "A", status });

describe("countSeating", () => {
  it("counts each reply and a missing status as no reply", () => {
    const t = countSeating([
      { name: "1", seats: [seat("confirmed"), seat("confirmed"), seat("pending")] },
      { name: "2", seats: [seat("declined"), seat(), seat()] },
    ]);
    expect(t).toEqual({ tables: 2, guests: 6, confirmed: 2, pending: 1, declined: 1, noReply: 2 });
    expect(t.confirmed + t.pending + t.declined + t.noReply).toBe(t.guests);
  });

  it("an empty table adds a table and no guests", () => {
    expect(countSeating([{ name: "1", seats: [] }])).toEqual({ tables: 1, guests: 0, confirmed: 0, pending: 0, declined: 0, noReply: 0 });
  });
});

describe("tableFill", () => {
  const seats = (n: number) => ({ seats: Array.from({ length: n }, () => seat()) });
  it("reads empty, partial, full and over", () => {
    expect(tableFill(seats(0), 10)).toMatchObject({ state: "empty", free: 10, over: 0 });
    expect(tableFill(seats(7), 10)).toMatchObject({ state: "partial", seated: 7, free: 3, over: 0 });
    expect(tableFill(seats(10), 10)).toMatchObject({ state: "full", free: 0, over: 0 });
    expect(tableFill(seats(12), 10)).toMatchObject({ state: "over", free: 0, over: 2 });
  });
});

describe("seatPositions", () => {
  it("puts the first seat at the top and spaces the rest evenly", () => {
    const p = seatPositions(4, 50, 50, 10);
    expect(p[0].x).toBeCloseTo(50);
    expect(p[0].y).toBeCloseTo(40);
    expect(p[1].x).toBeCloseTo(60);
    expect(p[2].y).toBeCloseTo(60);
    expect(seatPositions(0, 0, 0, 1)).toEqual([]);
  });
});
