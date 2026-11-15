import type { DefaultSession } from "next-auth";
import type { ManagedSession } from "@/server/management-session";

declare module "next-auth" {
  interface User {
    managedIdentity?: ManagedSession;
  }
  interface Session {
    // Exactly one is set: `discordId` after a Discord sign-in, `personId`
    // after a one-time code (DEC-287). `checkSession` turns either into the
    // same identity.toml person.
    user: { discordId?: string; personId?: string; sessionId?: string;
      managedIdentity?: ManagedSession } & DefaultSession["user"];
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    discordId?: string;
    personId?: string;
    sessionId?: string;
    managedIdentity?: ManagedSession;
  }
}
