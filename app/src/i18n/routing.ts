import { defineRouting } from "next-intl/routing";
import { createNavigation } from "next-intl/navigation";

// zh-Hant is the default: the circle talks in Cantonese-inflected Traditional
// Chinese, and so does Nora's own doctrine; English is the second audience.
// The public site (site/) makes the same choice and shares this hostname:
// it owns `/` and `/en/` exactly, so the portal's pages never live at a
// bare locale root (site/README.md, "Going live").
export const routing = defineRouting({
  locales: ["zh-Hant", "en"],
  defaultLocale: "zh-Hant",
  localePrefix: "always",
});

export type Locale = (typeof routing.locales)[number];

export const { Link, redirect, usePathname, useRouter, getPathname } = createNavigation(routing);
