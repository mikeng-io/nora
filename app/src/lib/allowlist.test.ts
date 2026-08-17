import { describe, expect, it } from "vitest";
import { resolveDiscordPerson } from "./allowlist";

const IDENTITY_TOML = `
[persons.miles]
name = "Miles"

[[persons.miles.signal]]
phone = "+85212345678"

[[persons.miles.discord]]
user_id = "100000000000000003"

[persons.ada]
name = "Ada"

[[persons.ada.discord]]
user_id = "988260089562885951"
`;

describe("resolveDiscordPerson", () => {
  it("resolves a known Discord snowflake to its person", () => {
    const result = resolveDiscordPerson(IDENTITY_TOML, "100000000000000003");
    expect(result).toEqual({ personId: "miles", displayName: "Miles" });
  });

  it("resolves a second person's snowflake independently", () => {
    const result = resolveDiscordPerson(IDENTITY_TOML, "988260089562885951");
    expect(result).toEqual({ personId: "ada", displayName: "Ada" });
  });

  it("returns null for an unknown snowflake", () => {
    expect(resolveDiscordPerson(IDENTITY_TOML, "000000000000000000")).toBeNull();
  });

  it("returns null for a person with no discord block at all", () => {
    const toml = `
[persons.signalonly]
name = "Signal Only"

[[persons.signalonly.signal]]
phone = "+85200000000"
`;
    expect(resolveDiscordPerson(toml, "100000000000000003")).toBeNull();
  });

  it("never matches on username, only user_id", () => {
    const toml = `
[persons.miles]
name = "Miles"

[[persons.miles.discord]]
user_id = "100000000000000003"
username = "pinegrove"
`;
    expect(resolveDiscordPerson(toml, "pinegrove")).toBeNull();
  });
});
