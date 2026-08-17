import { describe, expect, it } from "vitest";
import { formatCpuPercent, formatGb, formatUptime } from "./games-format";

describe("formatGb", () => {
  it("renders one decimal with a unit suffix", () => {
    expect(formatGb(5.4)).toBe("5.4 GB");
  });

  it("floors a negative or non-finite value at 0 GB", () => {
    expect(formatGb(-1)).toBe("0 GB");
    expect(formatGb(NaN)).toBe("0 GB");
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
