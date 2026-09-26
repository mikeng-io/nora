import { describe, expect, it } from "vitest";
import {
  dayIndex,
  formatAge,
  formatBytes,
  formatDistance,
  formatDuration,
  formatRelative,
  mediumAddress,
  partOfDay,
  shortAddress,
  zonedParts,
} from "./format";

// 2026-09-27 12:00 in Hong Kong (UTC+8) is 04:00Z.
const NOON_HK = Date.parse("2026-09-27T04:00:00Z");

describe("formatDuration", () => {
  it("keeps two units at most, in the reader's language", () => {
    expect(formatDuration(3 * 86400_000 + 4 * 3600_000 + 5 * 60_000, "en")).toBe("3d 4h");
    expect(formatDuration(2 * 3600_000 + 5 * 60_000, "en")).toBe("2h 5m");
    expect(formatDuration(40_000, "en")).toBe("40s");
    expect(formatDuration(2 * 3600_000 + 5 * 60_000, "zh-Hant")).toBe("2小時5分");
    expect(formatDuration(-5, "en")).toBe("0s");
  });
});

describe("formatAge", () => {
  it("is compact and never mixes languages", () => {
    expect(formatAge(20, "en")).toBe("now");
    expect(formatAge(20, "zh-Hant")).toBe("啱啱");
    expect(formatAge(4 * 60, "en")).toBe("4m");
    expect(formatAge(3 * 3600, "zh-Hant")).toBe("3小時");
  });
});

describe("zoned time", () => {
  it("reads the Hong Kong wall clock whatever the machine's zone", () => {
    expect(zonedParts(NOON_HK)).toEqual({ hour: 12, minute: 0, weekday: 6 });
    expect(partOfDay(NOON_HK)).toBe("afternoon");
  });

  it("calls yesterday yesterday across the zone's midnight", () => {
    const lateYesterday = Date.parse("2026-09-26T15:30:00Z"); // 23:30 HK on the 26th
    expect(dayIndex(NOON_HK) - dayIndex(lateYesterday)).toBe(1);
    expect(formatRelative(lateYesterday - 60 * 60_000 * 20, "en", NOON_HK)).toMatch(/yesterday|2 days ago/);
  });

  it("says minutes and hours for recent times", () => {
    expect(formatRelative(NOON_HK - 5 * 60_000, "en", NOON_HK)).toBe("5 minutes ago");
    expect(formatRelative(NOON_HK + 2 * 3600_000, "en", NOON_HK)).toBe("in 2 hours");
    expect(formatRelative(NOON_HK - 10_000, "en", NOON_HK)).toBe("now");
  });
});

describe("Cantonese dates", () => {
  it("says tomorrow and now the way the group does, not in Mandarin", () => {
    const tomorrow = formatRelative(NOON_HK + 26 * 3600_000, "zh-Hant", NOON_HK);
    expect(tomorrow).toBe("聽日");
    expect(formatRelative(NOON_HK - 5_000, "zh-Hant", NOON_HK)).toBe("而家");
    expect(formatRelative(NOON_HK + 50 * 3600_000, "zh-Hant", NOON_HK)).toBe("後日");
  });
});

describe("numbers and places", () => {
  it("formats bytes and distances", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(5.8 * 1024 ** 3)).toBe("5.8 GB");
    expect(formatDistance(1234, "en")).toBe("1.2 km");
    expect(formatDistance(87, "en")).toBe("90 m");
  });

  it("shortens Life360 addresses", () => {
    expect(shortAddress("Nathan Road, Mong Kok, Hong Kong")).toBe("Nathan Road");
    expect(mediumAddress("Nathan Road, Mong Kok, Hong Kong")).toBe("Nathan Road, Mong Kok");
    expect(shortAddress(undefined)).toBeNull();
  });
});
