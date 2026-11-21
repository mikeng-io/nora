import { defineRouting } from "next-intl/routing";
import { createNavigation } from "next-intl/navigation";
import { DEFAULT_LOCALE, LOCALES } from "./locales";

// zh-Hant is the default: the circle talks in Cantonese-inflected Traditional
// Chinese, and so does Nora's own doctrine; English is the second audience.
export const routing = defineRouting({
  locales: LOCALES,
  defaultLocale: DEFAULT_LOCALE,
  localePrefix: "always",
  // The locale preference is not a secret (HttpOnly would buy nothing the
  // rendered page does not already say), but it IS browser state and must not
  // ride a plaintext request: mark it Secure. NextAuth's session cookies are
  // already HttpOnly; Secure; SameSite=Lax independently of this.
  localeCookie: { secure: true },
});

export type Locale = (typeof routing.locales)[number];

export const { Link, redirect, usePathname, useRouter, getPathname } = createNavigation(routing);
