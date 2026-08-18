import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  formatBytes,
  formatClockTime,
  formatCount,
  formatCpuPercent,
  formatDateTime,
  formatDurationMs,
  formatPercent,
  formatRate,
  formatRelativeTime,
  formatUptime,
  uptimeSecondsFrom,
} from "./games-format";

describe("formatBytes", () => {
  it("scales up through the unit ladder", () => {
    expect(formatBytes(512)).toBe("512.0 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(2_147_483_648)).toBe("2.0 GB");
  });

  it("floors a negative or non-finite value at 0 B", () => {
    expect(formatBytes(-1)).toBe("0 B");
    expect(formatBytes(NaN)).toBe("0 B");
  });
});

describe("formatRate", () => {
  it("appends /s to the byte scale", () => {
    expect(formatRate(2048)).toBe("2.0 KB/s");
  });
});

describe("formatCpuPercent", () => {
  it("renders one decimal with a percent sign", () => {
    expect(formatCpuPercent(12.44)).toBe("12.4%");
  });

  it("renders an em dash for a non-finite reading", () => {
    expect(formatCpuPercent(Infinity)).toBe("—");
  });
});

describe("formatUptime", () => {
  it("renders days+hours in English past a day", () => {
    expect(formatUptime(90_000)).toBe("1d 1h");
  });

  it("renders hours+minutes under a day", () => {
    expect(formatUptime(3_900)).toBe("1h 5m");
  });

  it("renders minutes only under an hour", () => {
    expect(formatUptime(120)).toBe("2m");
  });

  it("uses Cantonese-register units for a zh-Hant reader", () => {
    expect(formatUptime(90_000, "zh-Hant")).toBe("1 天 1 小時");
  });

  it("floors a negative or non-finite value at 0", () => {
    expect(formatUptime(-5)).toBe("0m");
    expect(formatUptime(NaN)).toBe("0m");
  });
});

describe("uptimeSecondsFrom", () => {
  it("derives elapsed seconds from the sample's own clock", () => {
    const current = { timestamp: 1_000_000, startedAt: new Date(1_000_000 - 5_000).toISOString() };
    expect(uptimeSecondsFrom(current)).toBe(5);
  });

  it("returns 0 when startedAt is absent", () => {
    expect(uptimeSecondsFrom({ timestamp: 1_000_000 })).toBe(0);
  });
});

describe("formatDurationMs", () => {
  it("scales through the same ladder as formatUptime, from milliseconds", () => {
    expect(formatDurationMs(90_000_000)).toBe("1d 1h");
    expect(formatDurationMs(3_900_000)).toBe("1h 5m");
  });

  it("floors a negative or non-finite value at 0", () => {
    expect(formatDurationMs(-5)).toBe("0m");
    expect(formatDurationMs(NaN)).toBe("0m");
  });
});

describe("formatPercent", () => {
  it("renders a 0-1 fraction as a whole percent", () => {
    expect(formatPercent(0.862)).toBe("86%");
  });

  it("renders an em dash for a non-finite value", () => {
    expect(formatPercent(NaN)).toBe("—");
  });
});

describe("formatCount", () => {
  it("groups thousands", () => {
    expect(formatCount(6649)).toBe("6,649");
  });
});

describe("formatRelativeTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-19T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders a minute-register diff under an hour", () => {
    const fiveMinAgo = Date.now() - 5 * 60_000;
    expect(formatRelativeTime(fiveMinAgo, "en")).toBe("5 minutes ago");
  });

  it("renders an hour-register diff under a day", () => {
    const threeHoursAgo = Date.now() - 3 * 3_600_000;
    expect(formatRelativeTime(threeHoursAgo, "en")).toBe("3 hours ago");
  });

  it("renders a day-register diff past a day", () => {
    const twoDaysAgo = Date.now() - 2 * 86_400_000;
    expect(formatRelativeTime(twoDaysAgo, "en")).toBe("2 days ago");
  });
});

describe("formatClockTime / formatDateTime", () => {
  it("render in a fixed timezone regardless of locale's default zone", () => {
    // 2026-08-19T04:30:00Z == 12:30 in Asia/Hong_Kong (UTC+8).
    const ts = Date.parse("2026-08-19T04:30:00Z");
    expect(formatClockTime(ts, "en")).toBe("12:30 PM");
    expect(formatDateTime(ts, "en")).toContain("12:30 PM");
    expect(formatDateTime(ts, "en")).toContain("Aug");
  });
});
