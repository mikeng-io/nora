import { defineRouting } from "next-intl/routing";
import { createNavigation } from "next-intl/navigation";

// zh-Hant default: the friend group actually talks in Cantonese-inflected
// Traditional Chinese (see real message content flowing through Nora
// herself, and her own doctrine, which is written the same way) — English
// is the secondary audience, not the primary one.
export const routing = defineRouting({
  locales: ["zh-Hant", "en"],
  defaultLocale: "zh-Hant",
  localePrefix: "always",
});

export const { Link, redirect, usePathname, useRouter, getPathname } =
  createNavigation(routing);
