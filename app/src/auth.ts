import NextAuth from "next-auth";
import Discord from "next-auth/providers/discord";

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [Discord],
  callbacks: {
    // Discord's OAuth `providerAccountId` IS the snowflake
    // (design/10-identity-and-policy-schema.md's "the snowflake, via
    // Developer Mode") — carried onto the JWT on first sign-in only, since
    // `account` is undefined on every subsequent call.
    async jwt({ token, account }) {
      if (account?.provider === "discord") {
        token.discordId = account.providerAccountId;
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
