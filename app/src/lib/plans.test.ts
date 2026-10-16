import { describe, expect, it } from "vitest";
import type { Plan } from "./api/people";
import { pastPlans, planToIcs, upcomingPlans } from "./plans";
import { orderEpisodes, parseEpisode } from "./memory";

const NOW = Date.parse("2026-09-27T12:00:00+08:00");
const plan = (id: string, when: string | null, status: Plan["status"] = "confirmed"): Plan => ({
  id,
  title: `Plan ${id}`,
  when,
  status,
  venue: { text: null, name: "Hotpot place", address: "1 Main St, Yuen Long", rating: null, price_level: "", maps_url: null },
  created_by: "miles",
  created_at: "2026-09-01T00:00:00+08:00",
});

describe("plans in time", () => {
  const plans = [
    plan("later", "2026-10-02T19:00:00+08:00"),
    plan("soon", "2026-09-28T19:00:00+08:00"),
    plan("undated", null, "proposed"),
    plan("dinner-started", "2026-09-27T10:30:00+08:00"),
    plan("past", "2026-09-20T19:00:00+08:00"),
    plan("cancelled", "2026-09-30T19:00:00+08:00", "cancelled"),
  ];

  it("orders what is coming soonest first, undated last, and keeps a plan that just started", () => {
    expect(upcomingPlans(plans, NOW).map((p) => p.id)).toEqual(["dinner-started", "soon", "later", "undated"]);
  });

  it("puts past and cancelled plans in the past, newest first", () => {
    expect(pastPlans(plans, NOW).map((p) => p.id)).toEqual(["cancelled", "past"]);
  });

  it("writes a calendar file with the venue and a UTC start", () => {
    const ics = planToIcs(plans[1], NOW)!;
    expect(ics).toContain("DTSTART:20260928T110000Z");
    expect(ics).toContain("LOCATION:Hotpot place\\, 1 Main St\\, Yuen Long");
    expect(ics.endsWith("END:VCALENDAR\r\n")).toBe(true);
    expect(planToIcs(plans[2], NOW)).toBeNull();
  });
});

describe("episodes", () => {
  it("pulls a leading date out and leaves the rest alone", () => {
    expect(parseEpisode("2026-09-20 — asked about hotpot")).toMatchObject({ date: "2026-09-20", text: "asked about hotpot" });
    expect(parseEpisode("likes hotpot")).toMatchObject({ date: null, text: "likes hotpot" });
  });

  it("orders dated episodes newest first, undated after in their own order", () => {
    const out = orderEpisodes(["b", "2026-09-01: old", "a", "2026-09-10 — new"]).map((e) => e.text);
    expect(out).toEqual(["new", "old", "b", "a"]);
  });
});

describe("every status the backend sends", () => {
  it("parses a list holding a lapsed plan instead of failing the whole page", async () => {
    const { PlansSchema } = await import("./api/people");
    for (const status of ["staged", "proposed", "confirmed", "happened", "cancelled", "lapsed", "evicted"]) {
      expect(PlansSchema.safeParse([plan("x", null, status as Plan["status"])]).success, status).toBe(true);
    }
  });

  it("puts a plan that is over or off in the past even when its date is ahead", () => {
    const ahead = "2026-10-02T19:00:00+08:00";
    const ended = (["happened", "lapsed", "evicted"] as const).map((s) => plan(s, ahead, s));
    expect(upcomingPlans(ended, NOW)).toEqual([]);
    expect(pastPlans(ended, NOW).map((p) => p.id).sort()).toEqual(["evicted", "happened", "lapsed"]);
  });
});

describe("a calendar export says what the plan is", () => {
  it("is cancelled for a plan that is off and tentative for one not yet settled", () => {
    const at = "2026-10-02T19:00:00+08:00";
    expect(planToIcs(plan("c", at, "cancelled"), NOW)).toContain("STATUS:CANCELLED");
    expect(planToIcs(plan("l", at, "lapsed"), NOW)).toContain("STATUS:CANCELLED");
    expect(planToIcs(plan("s", at, "staged"), NOW)).toContain("STATUS:TENTATIVE");
    expect(planToIcs(plan("h", at, "happened"), NOW)).toContain("STATUS:CONFIRMED");
  });
});
