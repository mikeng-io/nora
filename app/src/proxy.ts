import createMiddleware from "next-intl/middleware";
import { routing } from "./i18n/routing";

// Locale routing only. The sign-in gate is NOT here: it lives in the
// (portal) layout and in every /api/v1 read (src/server/session.ts), both of
// which run in Node and can read identity.toml.
export default createMiddleware(routing);

export const config = {
  // Everything except the API (Auth.js's Discord callback must stay
  // unprefixed), Next internals, and any file with an extension (the service
  // worker, the manifest, icons, the offline page).
  matcher: ["/((?!api|_next|_vercel|.*\\..*).*)"],
};
