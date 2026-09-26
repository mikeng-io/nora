import { describe, expect, it } from "vitest";
import type { Tool } from "./api/nora";
import { effectKey, matchesFilter, matchesQuery, tierKey } from "./tools";

const tool = (over: Partial<Tool>): Tool => ({
  name: "weather",
  description: "Weather from the Observatory",
  budget: 2,
  terminal: false,
  time_shiftable: null,
  source_tier: "confirmed",
  cacheable: true,
  read_only: true,
  side_effect_state: "none",
  idempotency: "idempotent",
  retry: "safe",
  output_contract: null,
  ...over,
});

describe("tool filters", () => {
  it("sorts tools by where their answer comes from, and by whether they write", () => {
    expect(matchesFilter(tool({}), "live")).toBe(true);
    expect(matchesFilter(tool({}), "remembered")).toBe(false);
    expect(matchesFilter(tool({ source_tier: "remembered" }), "remembered")).toBe(true);
    expect(matchesFilter(tool({ source_tier: null, read_only: false }), "writes")).toBe(true);
    expect(matchesFilter(tool({}), "writes")).toBe(false);
    expect(matchesFilter(tool({ source_tier: null }), "all")).toBe(true);
  });

  it("never guesses a data source for an unknown tier", () => {
    expect(tierKey(null)).toBe("action");
    expect(tierKey("something-new")).toBe("action");
    expect(tierKey("web")).toBe("web");
    expect(effectKey("something-new")).toBeNull();
  });

  it("matches every word of a query anywhere in the text", () => {
    expect(matchesQuery("weather Weather from the Observatory", "observatory weather")).toBe(true);
    expect(matchesQuery("weather", "bus")).toBe(false);
    expect(matchesQuery("anything", "  ")).toBe(true);
  });
});
