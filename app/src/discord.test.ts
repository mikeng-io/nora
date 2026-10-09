import { describe, expect, it } from "vitest";
import { discordProvider } from "./discord";

describe("the Discord provider", () => {
  it("declares Discord's own issuer, so the RFC 9207 iss check can pass", () => {
    // Discord sends `iss` on its callback since 2026-10; with no issuer declared,
    // next-auth compared it against its placeholder and refused every sign-in.
    // Literal value, from https://discord.com/.well-known/oauth-authorization-server.
    const p = discordProvider as unknown as { id: string; options?: { issuer?: string } };
    expect(p.id).toBe("discord");
    expect(p.options?.issuer).toBe("https://discord.com");
  });
});
