import NextAuth, { type NextAuthConfig } from "next-auth";
import { randomUUID } from "node:crypto";
import { discordProvider } from "./discord";
import Credentials from "next-auth/providers/credentials";
import { codeSignInEnabled, verifyCode } from "@/server/otp";
import { captureManagedSession, identityBackend, managedSessionSchema,
  type AuthenticationEvent } from "@/server/management-session";
import { applyConsentIntent, linkConsentOutcomeSchema } from "@/server/link-consent";
import { createTelegramProvider } from "./telegram";
import { verifyManagedCode } from "@/server/managed-otp";

// Development sign-in, for working on the portal without a Discord app:
// pick a person from dev/identity.dev.toml and be them. It exists only when
// BOTH hold: `next dev` (NODE_ENV is "development"; `next build` and
// `next start` force "production") and NORA_DEV_LOGIN=1. It cannot be
// switched on in the container, which runs `node server.js` from a
// production build.
export const DEV_LOGIN_ENABLED =
  identityBackend() === "file" && process.env.NODE_ENV === "development"
  && process.env.NORA_DEV_LOGIN === "1";

const providers: NextAuthConfig["providers"] = [discordProvider];
export const TELEGRAM_LOGIN_ENABLED = identityBackend() === "managed"
  && !!process.env.AUTH_TELEGRAM_ID && !!process.env.AUTH_TELEGRAM_SECRET;
if (TELEGRAM_LOGIN_ENABLED) {
  providers.push(createTelegramProvider({ clientId: process.env.AUTH_TELEGRAM_ID!,
    clientSecret: process.env.AUTH_TELEGRAM_SECRET! }));
}

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
        if (identityBackend() === "managed") {
          const managedIdentity = await verifyManagedCode({ challengeId, code });
          return managedIdentity ? { id: managedIdentity.binding.person_id, managedIdentity } : null;
        }
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

export const { handlers, auth, signIn, signOut } = NextAuth((request) => ({
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
      if (account) {
        const sessionId = randomUUID();
        token.sessionId = sessionId;
        delete token.managedIdentity;
        delete token.discordId;
        delete token.personId;
        if (identityBackend() === "managed") {
          const platform = account.provider;
          if (platform === "otp") {
            const managed = managedSessionSchema.safeParse(user?.managedIdentity);
            if (!managed.success || !["signal_code", "whatsapp_code"].includes(
              managed.data.authentication.method)) {
              throw new Error("Managed sign-in requires account-exact authentication");
            }
            token.managedIdentity = managed.data;
            token.sessionId = managed.data.authentication.sessionId;
            return token;
          }
          if (platform !== "discord" && platform !== "telegram") {
            throw new Error("Managed sign-in requires account-exact authentication");
          }
          const authentication: AuthenticationEvent = {
            sessionId,
            account: { platform, kind: "user_id", value: account.providerAccountId },
            method: platform === "discord" ? "discord_oauth" : "telegram_oidc",
            proofIssuer: (platform === "discord" ? process.env.AUTH_DISCORD_ID
              : process.env.AUTH_TELEGRAM_ID) ?? "",
            authenticatedAt: Math.floor(Date.now() / 1000),
          };
          token.managedIdentity = await captureManagedSession(authentication);
          // Only this genuine fresh provider callback may spend a consent
          // intent; the fresh event — never a retained session — signs it.
          const callback = request ? new URL(request.url) : undefined;
          const callbackState = callback?.pathname === `/api/auth/callback/${platform}`
            ? callback.searchParams.get("state") ?? undefined : undefined;
          const consent = await applyConsentIntent(authentication, callbackState);
          if (consent !== undefined) token.linkConsent = consent;
          return token;
        }
      }
      if (account?.provider === "discord") token.discordId = account.providerAccountId;
      if (account?.provider === "dev" && user?.id) token.discordId = user.id;
      if (account?.provider === "otp" && user?.id) token.personId = user.id;
      return token;
    },
    async session({ session, token }) {
      if (typeof token.sessionId === "string") session.user.sessionId = token.sessionId;
      if (typeof token.discordId === "string") session.user.discordId = token.discordId;
      if (typeof token.personId === "string") session.user.personId = token.personId;
      const managed = managedSessionSchema.safeParse(token.managedIdentity);
      if (managed.success) session.user.managedIdentity = managed.data;
      const consent = linkConsentOutcomeSchema.safeParse(token.linkConsent);
      if (consent.success) session.user.linkConsent = consent.data;
      return session;
    },
  },
}));
