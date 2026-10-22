import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const SECURITY_HEADERS = [
  // Baseline CSP: Next hydration, map workers and OAuth keep their existing sources.
  { key: "Content-Security-Policy", value: "base-uri 'self'; object-src 'none'; frame-ancestors 'none'" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
];

// Pages from the first portal, so a bookmark or an installed app's old
// start URL lands on the page that now holds the same thing.
const MOVED: Array<[string, string]> = [
  ["/:locale(zh-Hant|en)/status", "/:locale/nora"],
  ["/:locale(zh-Hant|en)/memory", "/:locale/nora/memory"],
  ["/:locale(zh-Hant|en)/doctrine", "/:locale/nora/doctrine"],
  ["/:locale(zh-Hant|en)/tools", "/:locale/nora/abilities"],
  ["/:locale(zh-Hant|en)/events", "/:locale/plans"],
  ["/:locale(zh-Hant|en)/events/:id", "/:locale/plans/:id"],
  ["/:locale(zh-Hant|en)/games/:id/players", "/:locale/games/:id?tab=players"],
  ["/:locale(zh-Hant|en)/games/:id/activity", "/:locale/games/:id?tab=activity"],
  ["/:locale(zh-Hant|en)/games/:id/sessions", "/:locale/games/:id?tab=activity"],
];

const nextConfig: NextConfig = {
  output: "standalone",
  // The repo's AGENTS.md is the one entry point for agents (ADR 0010); a
  // second one generated here by `next dev` would drift from it.
  agentRules: false,
  poweredByHeader: false,
  devIndicators: false,
  reactStrictMode: true,
  images: { formats: ["image/avif", "image/webp"] },
  async redirects() {
    return MOVED.map(([source, destination]) => ({ source, destination, permanent: false }));
  },
  async headers() {
    return [
      { source: "/:path*", headers: SECURITY_HEADERS },
      {
        // The worker must never be served from an HTTP cache, or an update
        // waits on the cache's expiry instead of the next visit.
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Service-Worker-Allowed", value: "/" },
        ],
      },
    ];
  },
};

export default withNextIntl(nextConfig);
