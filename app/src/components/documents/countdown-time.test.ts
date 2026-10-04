import { describe, expect, it } from "vitest";
import { hkDate, remaining, targetInstant } from "./countdown-time";

// 2026-10-10 09:00 Hong Kong is 01:00 UTC.
const at = (iso: string) => Date.parse(iso);

describe("targetInstant", () => {
  it("reads the date in Hong Kong time", () => {
    expect(targetInstant("2026-10-10", "09:00")).toBe(at("2026-10-10T01:00:00Z"));
    expect(targetInstant("2026-10-10")).toBe(at("2026-10-09T16:00:00Z"));
    expect(targetInstant("nope")).toBeNaN();
  });
});

describe("hkDate", () => {
  it("rolls to the next day at 16:00 UTC", () => {
    expect(hkDate(at("2026-10-09T15:59:00Z"))).toBe("2026-10-09");
    expect(hkDate(at("2026-10-09T16:00:00Z"))).toBe("2026-10-10");
  });
});

describe("remaining", () => {
  it("counts whole days when a day or more is left", () => {
    expect(remaining("2026-10-10", "09:00", at("2026-09-30T01:00:00Z"))).toEqual({ kind: "days", days: 10 });
    expect(remaining("2026-10-10", "09:00", at("2026-10-08T23:00:00Z"))).toEqual({ kind: "days", days: 1 });
  });

  it("shows hours and minutes under a day, rounding a part minute up", () => {
    expect(remaining("2026-10-10", "09:00", at("2026-10-09T23:00:00Z"))).toEqual({ kind: "hours", hours: 2, minutes: 0 });
    expect(remaining("2026-10-10", "09:00", at("2026-10-10T00:29:30Z"))).toEqual({ kind: "hours", hours: 0, minutes: 31 });
    expect(remaining("2026-10-10", "09:00", at("2026-10-10T00:59:59Z"))).toEqual({ kind: "hours", hours: 0, minutes: 1 });
  });

  it("is today on the event's day once it has started, passed the day after", () => {
    expect(remaining("2026-10-10", "09:00", at("2026-10-10T01:00:00Z"))).toEqual({ kind: "today" });
    expect(remaining("2026-10-10", "09:00", at("2026-10-10T10:00:00Z"))).toEqual({ kind: "today" });
    expect(remaining("2026-10-10", undefined, at("2026-10-10T05:00:00Z"))).toEqual({ kind: "today" });
    expect(remaining("2026-10-10", "09:00", at("2026-10-10T16:00:00Z"))).toEqual({ kind: "passed" });
    expect(remaining("2026-10-10", undefined, at("2026-10-10T16:00:00Z"))).toEqual({ kind: "passed" });
  });

  it("reads the day in Hong Kong, not UTC", () => {
    // 00:30 HKT on the 10th is still the 9th in UTC.
    expect(remaining("2026-10-10", "23:00", at("2026-10-09T16:30:00Z"))).toEqual({ kind: "hours", hours: 22, minutes: 30 });
  });

  it("is null for a date it cannot read", () => {
    expect(remaining("2026-13-45", undefined, 0)).toBeNull();
  });
});
