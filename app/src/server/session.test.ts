import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { personById } from "./allowlist";

// Every way in ends as ONE identity.toml person (DEC-287), re-read on every
// request: these pin that, and that somebody removed from the file is out.

vi.mock("server-only", () => ({}));
const session = vi.hoisted(() => ({ current: null as null | { user: Record<string, unknown> } }));
vi.mock("@/auth", () => ({ auth: async () => session.current }));

const TOML = `
[persons.miles]
name = "Miles"
[[persons.miles.discord]]
user_id = "100000000000000001"

[persons.sam]
name = "Sam"
`;

const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "identity-")), "identity.toml");
fs.writeFileSync(file, TOML);
process.env.IDENTITY_TOML_PATH = file;
const { checkSession } = await import("./session");

describe("personById", () => {
  it("finds a person in the file, and nothing on the object's prototype", () => {
    expect(personById(TOML, "sam")).toEqual({ personId: "sam", displayName: "Sam" });
    expect(personById(TOML, "nobody")).toBeNull();
    expect(personById(TOML, "constructor")).toBeNull();
    expect(personById(TOML, "__proto__")).toBeNull();
  });
});

describe("checkSession", () => {
  beforeEach(() => {
    session.current = null;
  });

  it("a code session and a Discord session are the same person", async () => {
    session.current = { user: { personId: "miles" } };
    const byCode = await checkSession();
    session.current = { user: { discordId: "100000000000000001" } };
    const byDiscord = await checkSession();
    expect(byCode).toMatchObject({ status: "ok", personId: "miles", via: "code" });
    expect(byDiscord).toMatchObject({ status: "ok", personId: "miles", via: "discord" });
  });

  it("refuses a code session whose person is no longer in the file", async () => {
    session.current = { user: { personId: "gone" } };
    expect(await checkSession()).toMatchObject({ status: "denied", via: "code" });
  });

  it("refuses an unknown Discord account, and has no session without either id", async () => {
    session.current = { user: { discordId: "999", name: "Stranger" } };
    expect(await checkSession()).toMatchObject({ status: "denied", via: "discord", name: "Stranger" });
    session.current = { user: {} };
    expect(await checkSession()).toEqual({ status: "no-session" });
  });
});
