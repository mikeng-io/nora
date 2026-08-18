import { describe, expect, it } from "vitest";
import { SkillsSchema } from "./skills-schema";

describe("SkillsSchema", () => {
  it("parses a real /app/v1/skills response", () => {
    const body = [{ name: "navigation", description: "Load when planning a journey." }];
    expect(() => SkillsSchema.parse(body)).not.toThrow();
  });

  it("parses an empty array when no skills are configured", () => {
    expect(() => SkillsSchema.parse([])).not.toThrow();
  });

  it("rejects an entry missing description", () => {
    expect(() => SkillsSchema.parse([{ name: "navigation" }])).toThrow();
  });
});
