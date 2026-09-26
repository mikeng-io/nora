import { describe, expect, it } from "vitest";
import type { Session } from "./api/games";
import { playtimeByDay, serverState, sundayFirstToMondayFirst, uptimeMs } from "./games";
import { dayIndex } from "./format";

const session = (joined: string, left: string | null): Session => ({
  id: 1,
  serverId: "a",
  playerKey: "p",
  playerId: "p",
  playerName: "P",
  joinedAt: Date.parse(joined),
  leftAt: left ? Date.parse(left) : null,
  closedReason: null,
  durationMs: null,
});

describe("serverState", () => {
  const cur = (online: boolean, status: string) => ({ online, status }) as never;
  it("tells a restart from a stop, and a local save from both", () => {
    expect(serverState({ current: cur(true, "running") })).toBe("online");
    expect(serverState({ current: cur(false, "restarting") })).toBe("restarting");
    expect(serverState({ current: cur(false, "exited") })).toBe("offline");
    expect(serverState({ current: null })).toBe("local");
  });
});

describe("uptimeMs", () => {
  it("reads the sample's own clock and refuses a bad date", () => {
    expect(uptimeMs({ timestamp: 10_000, startedAt: new Date(4_000).toISOString() })).toBe(6_000);
    expect(uptimeMs({ timestamp: 10_000, startedAt: "not a date" })).toBeNull();
    expect(uptimeMs({ timestamp: 10_000 })).toBeNull();
  });
});

describe("playtimeByDay", () => {
  const now = Date.parse("2026-09-27T12:00:00+08:00");
  it("splits a session across Hong Kong midnight", () => {
    const days = playtimeByDay([session("2026-09-26T23:00:00+08:00", "2026-09-27T01:30:00+08:00")], 3, now);
    const byDay = Object.fromEntries(days.map((d) => [d.day - dayIndex(now), d.ms / 60_000]));
    expect(byDay).toEqual({ "-2": 0, "-1": 60, "0": 90 });
  });

  it("counts an open session up to now", () => {
    const days = playtimeByDay([session("2026-09-27T11:00:00+08:00", null)], 1, now);
    expect(days[0].ms).toBe(3_600_000);
  });
});

describe("weekday order", () => {
  it("moves Sunday to the end of a Monday-first week", () => {
    expect([0, 1, 6].map(sundayFirstToMondayFirst)).toEqual([6, 0, 5]);
  });
});
