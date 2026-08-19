import { describe, expect, it } from "vitest";
import { PersonProfileSchema, PersonsRosterSchema } from "./persons-schema";

describe("PersonsRosterSchema", () => {
  it("parses a real /app/v1/persons response shape", () => {
    const body = [
      { personId: "miles_novak", displayName: "Miles Novak" },
      { personId: "aaron_larsen", displayName: "Aaron Larsen" },
    ];
    expect(() => PersonsRosterSchema.parse(body)).not.toThrow();
  });

  it("rejects an entry missing displayName", () => {
    expect(() => PersonsRosterSchema.parse([{ personId: "miles_novak" }])).toThrow();
  });
});

describe("PersonProfileSchema", () => {
  it("parses a profile with game profiles", () => {
    const body = {
      personId: "miles_novak", displayName: "Miles Novak",
      gameProfiles: [{ serverId: "big_walk", displayName: "Big Walk", game: "big-walk" }],
    };
    expect(() => PersonProfileSchema.parse(body)).not.toThrow();
  });

  it("parses a profile with no game profiles", () => {
    const body = { personId: "miles_novak", displayName: "Miles Novak", gameProfiles: [] };
    expect(() => PersonProfileSchema.parse(body)).not.toThrow();
  });

  it("rejects a game profile missing game", () => {
    const body = {
      personId: "miles_novak", displayName: "Miles Novak",
      gameProfiles: [{ serverId: "big_walk", displayName: "Big Walk" }],
    };
    expect(() => PersonProfileSchema.parse(body)).toThrow();
  });
});
