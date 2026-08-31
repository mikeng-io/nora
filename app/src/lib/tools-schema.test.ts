import { describe, expect, it } from "vitest";
import { ToolEntrySchema, sideEffectLabel, tierLabel } from "./tools-schema";

const SAMPLE = {
  name: "media_fetch",
  description: "Look at an image or video and say what it shows.",
  budget: 4,
  terminal: false,
  time_shiftable: null,
  source_tier: null,
  cacheable: false,
  read_only: true,
  side_effect_state: "none",
  idempotency: "idempotent",
  retry: "safe",
  output_contract: null,
};

describe("ToolEntrySchema", () => {
  it("accepts a full backend payload", () => {
    expect(ToolEntrySchema.parse(SAMPLE)).toEqual(SAMPLE);
  });

  it("accepts a payload without description (un-introspectable tool)", () => {
    const { description: _drop, ...rest } = SAMPLE;
    const parsed = ToolEntrySchema.parse(rest);
    expect(parsed.description).toBeUndefined();
  });

  it("rejects an entry missing declared fields", () => {
    expect(() => ToolEntrySchema.parse({ name: "now" })).toThrow();
  });
});

describe("sideEffectLabel", () => {
  it("maps the declared vocabulary to reader-facing labels", () => {
    expect(sideEffectLabel("none")).toBe("Cannot write outside the turn");
    expect(sideEffectLabel("confirmed")).toBe("Writes are confirmed");
    expect(sideEffectLabel("unknown")).toBe("Write outcome unknown on timeout");
  });

  it("passes through unknown values rather than crashing", () => {
    expect(sideEffectLabel("new-state")).toBe("new-state");
  });
});

describe("tierLabel", () => {
  it("maps the three fact-source tiers", () => {
    expect(tierLabel("confirmed")).toBe("Live data");
    expect(tierLabel("remembered")).toBe("Household memory");
    expect(tierLabel("web")).toBe("Web");
  });

  it("labels non-fact-source tools as actions", () => {
    expect(tierLabel(null)).toBe("Action / pure function");
  });
});
