import Discord from "next-auth/providers/discord";

// Discord sends an RFC 9207 `iss` on its OAuth callback since 2026-10, and
// next-auth 5.0.0-beta.32's Discord provider declares no issuer, so Auth.js
// compared it against its placeholder and refused every sign-in
// (nextauthjs/next-auth#13504). The value is Discord's own, from
// https://discord.com/.well-known/oauth-authorization-server.
export const discordProvider = Discord({ issuer: "https://discord.com" });
