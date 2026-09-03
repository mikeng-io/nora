import { describe, expect, it } from "vitest";
import { SkillEntrySchema } from "./skills-schema";

const SAMPLE = {
  name: "personalization",
  description: "Load when a message asks you to recommend, compare, or weigh something for the people in this room.",
  instructions: "# Personalization\n\nAn answer that names something for these people is a claim about them.",
};

describe("SkillEntrySchema", () => {
  it("accepts a full backend payload", () => {
    expect(SkillEntrySchema.parse(SAMPLE)).toEqual(SAMPLE);
  });

  it("accepts an entry without the instructions body (pre-PR93 backend)", () => {
    const { instructions: _drop, ...rest } = SAMPLE;
    expect(SkillEntrySchema.parse(rest)).toEqual(rest);
  });

  it("rejects an entry missing the description", () => {
    const { description: _drop, ...rest } = SAMPLE;
    expect(() => SkillEntrySchema.parse(rest)).toThrow();
  });
});
