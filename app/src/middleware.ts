import createMiddleware from "next-intl/middleware";
import { routing } from "./i18n/routing";

// Locale routing ONLY — this is deliberately not where the identity.toml
// auth gate lives (see (portal)/layout.tsx's own comment for why: Node
// middleware runtime doesn't actually work on this Next.js version). This
// middleware never touches node:fs, so the default Edge runtime is fine for
// it — that was never the problem, the auth+fs combination was.
export default createMiddleware(routing);

export const config = {
  // Excludes API routes (Auth.js's callback URLs must stay un-prefixed —
  // Discord's redirect_uri is registered as /api/auth/callback/discord,
  // not /en/api/auth/...), Next internals, the PWA manifest/service worker,
  // and static files.
  matcher: ["/((?!api|_next|_vercel|manifest.json|sw.js|.*\\..*).*)"],
};
