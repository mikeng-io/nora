import NextAuth, { type NextAuthConfig } from "next-auth";
import { randomUUID } from "node:crypto";
import { discordProvider } from "./discord";
import Credentials from "next-auth/providers/credentials";
import { codeSignInEnabled, verifyCode } from "@/server/otp";

// Development sign-in, for working on the portal without a Discord app:
// pick a person from dev/identity.dev.toml and be them. It exists only when
// BOTH hold: `next dev` (NODE_ENV is "development"; `next build` and
// `next start` force "production") and NORA_DEV_LOGIN=1. It cannot be
// switched on in the container, which runs `node server.js` from a
// production build.
export const DEV_LOGIN_ENABLED =
  process.env.NODE_ENV === "development" && process.env.NORA_DEV_LOGIN === "1";

const providers: NextAuthConfig["providers"] = [discordProvider];

// A one-time code Nora sent to the person's own Signal or WhatsApp account
// (DEC-287). Nora checks it and answers who it was for, a person_id from
// identity.toml: the same id a Discord sign-in resolves to, so whichever way
// somebody signs in, they are the same person here. Present only when the
// portal holds Nora's OTP token.
if (codeSignInEnabled()) {
  providers.push(
    Credentials({
      id: "otp",
      name: "One-time code",
      credentials: {
        challengeId: { label: "Challenge", type: "text" },
        code: { label: "Code", type: "text" },
      },
      authorize: async (credentials) => {
        const challengeId = String(credentials?.challengeId ?? "");
        const code = String(credentials?.code ?? "");
        if (!challengeId || !/^[0-9]{6}$/.test(code)) return null;
        const personId = await verifyCode({ challengeId, code });
        return personId ? { id: personId } : null;
      },
    }),
  );
}
if (DEV_LOGIN_ENABLED) {
  providers.push(
    Credentials({
      id: "dev",
      name: "Development",
      credentials: { discordId: { label: "Discord id", type: "text" } },
      authorize: (credentials) => {
        const discordId = String(credentials?.discordId ?? "");
        return /^\d{5,24}$/.test(discordId) ? { id: discordId, name: `dev:${discordId}` } : null;
      },
    }),
  );
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers,
  session: { strategy: "jwt", maxAge: 60 * 60 * 24 * 30 },
  pages: { signIn: "/zh-Hant/login", error: "/zh-Hant/login" },
  trustHost: true,
  callbacks: {
    // Discord's OAuth `providerAccountId` IS the snowflake identity.toml
    // lists (design/10-identity-and-policy-schema.md), carried onto the JWT
    // on first sign-in only: `account` is undefined on every later call.
    // A code sign-in carries the person_id Nora verified instead: there is no
    // account at another provider to resolve, only the person.
    async jwt({ token, account, user }) {
      // Stable through JWT rotation, distinct even when the same person signs in again.
      if (account) token.sessionId = randomUUID();
      if (account?.provider === "discord") token.discordId = account.providerAccountId;
      if (account?.provider === "dev" && user?.id) token.discordId = user.id;
      if (account?.provider === "otp" && user?.id) token.personId = user.id;
      return token;
    },
    async session({ session, token }) {
      if (typeof token.sessionId === "string") session.user.sessionId = token.sessionId;
      if (typeof token.discordId === "string") session.user.discordId = token.discordId;
      if (typeof token.personId === "string") session.user.personId = token.personId;
      return session;
    },
  },
});
