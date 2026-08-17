import NextAuth from "next-auth";
import Discord from "next-auth/providers/discord";
import Credentials from "next-auth/providers/credentials";

// DEV/DEMO ONLY — never enabled unless AUTH_STUB=true is explicitly set.
// Lets you exercise the (portal) auth gate without a real Discord OAuth
// app. Default-off so a deploy that forgets to unset a leftover env var
// doesn't accidentally ship an auth bypass.
const stubProvider = Credentials({
  id: "stub",
  name: "Dev stub",
  credentials: { discordId: { label: "Discord snowflake", type: "text" } },
  async authorize(credentials) {
    const discordId = credentials?.discordId;
    if (typeof discordId !== "string" || !discordId) return null;
    return { id: discordId, name: "Dev stub user" };
  },
});

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: process.env.AUTH_STUB === "true" ? [Discord, stubProvider] : [Discord],
  callbacks: {
    // Discord's OAuth `providerAccountId` IS the snowflake
    // (design/10-identity-and-policy-schema.md's "the snowflake, via
    // Developer Mode") — carried onto the JWT on first sign-in only, since
    // `account` is undefined on every subsequent call. The stub provider
    // carries it the same way, via `user.id` instead of `providerAccountId`.
    async jwt({ token, account, user }) {
      if (account?.provider === "discord") {
        token.discordId = account.providerAccountId;
      } else if (account?.provider === "stub" && typeof user?.id === "string") {
        token.discordId = user.id;
      }
      return token;
    },
    async session({ session, token }) {
      if (typeof token.discordId === "string") {
        session.user.discordId = token.discordId;
      }
      return session;
    },
  },
});
