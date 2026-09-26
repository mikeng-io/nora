"use client";

import { useTransition } from "react";
import { useLocale, useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { clear as clearIdb } from "idb-keyval";
import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, type Locale } from "@/i18n/routing";
import { signOutAction } from "@/server/actions";
import { useTheme, type ThemeChoice } from "@/lib/theme";

export const LANGS: Array<{ locale: Locale; short: string; name: string }> = [
  { locale: "zh-Hant", short: "繁", name: "繁體中文" },
  { locale: "en", short: "EN", name: "English" },
];

// The same key the site's language switch writes (site/src/scripts/
// prefs.ts), so a choice made here also decides which landing page the
// site opens on.
const SITE_LANG_KEY = "nora-lang";

export function useLanguageSwitch() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const current = useLocale() as Locale;
  const [pending, start] = useTransition();
  const switchTo = (locale: Locale) => {
    if (locale === current) return;
    try {
      localStorage.setItem(SITE_LANG_KEY, locale);
    } catch {}
    const query = Object.fromEntries(params.entries());
    start(() => router.replace({ pathname, query }, { locale, scroll: false }));
  };
  return { current, switchTo, pending };
}

/** Signing out forgets everything this device kept: the persisted reads
 * (IndexedDB) and the pages the service worker cached. Then the session. */
export function useSignOut() {
  const locale = useLocale();
  const client = useQueryClient();
  const [pending, start] = useTransition();
  const signOut = () =>
    start(async () => {
      client.clear();
      await clearIdb().catch(() => {});
      if ("caches" in window) {
        const keys = await caches.keys().catch(() => [] as string[]);
        await Promise.all(keys.map((k) => caches.delete(k)));
      }
      navigator.serviceWorker?.controller?.postMessage("clear-caches");
      await signOutAction(locale);
    });
  return { signOut, pending };
}

export function useThemeLabels() {
  const t = useTranslations("account");
  const { choice, setChoice } = useTheme();
  const options: Array<{ value: ThemeChoice; label: string }> = [
    { value: "light", label: t("studio") },
    { value: "dark", label: t("night") },
    { value: "system", label: t("system") },
  ];
  return { choice, setChoice, options };
}
