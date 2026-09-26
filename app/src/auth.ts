import NextAuth, { type NextAuthConfig } from "next-auth";
import Discord from "next-auth/providers/discord";
import Credentials from "next-auth/providers/credentials";

// Development sign-in, for working on the portal without a Discord app:
// pick a person from dev/identity.dev.toml and be them. It exists only when
// BOTH hold: `next dev` (NODE_ENV is "development"; `next build` and
// `next start` force "production") and NORA_DEV_LOGIN=1. It cannot be
// switched on in the container, which runs `node server.js` from a
// production build.
export const DEV_LOGIN_ENABLED =
  process.env.NODE_ENV === "development" && process.env.NORA_DEV_LOGIN === "1";

const providers: NextAuthConfig["providers"] = [Discord];
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
    async jwt({ token, account, user }) {
      if (account?.provider === "discord") token.discordId = account.providerAccountId;
      if (account?.provider === "dev" && user?.id) token.discordId = user.id;
      return token;
    },
    async session({ session, token }) {
      if (typeof token.discordId === "string") session.user.discordId = token.discordId;
      return session;
    },
  },
});
