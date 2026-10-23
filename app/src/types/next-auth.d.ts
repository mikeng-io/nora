import type { DefaultSession } from "next-auth";

declare module "next-auth" {
  interface Session {
    // Exactly one is set: `discordId` after a Discord sign-in, `personId`
    // after a one-time code (DEC-287). `checkSession` turns either into the
    // same identity.toml person.
    user: { discordId?: string; personId?: string; sessionId?: string } & DefaultSession["user"];
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    discordId?: string;
    personId?: string;
    sessionId?: string;
  }
}
