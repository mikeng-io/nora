import { en } from "./en";
import { zhHant } from "./zh-hant";
import type { Dict } from "./types";

export type Locale = "zh-Hant" | "en";

interface LocaleInfo {
  /** Where this locale's home page lives on the site. */
  path: string;
  /** The <html lang> and hreflang value. */
  lang: string;
  /** Label in the language switch. */
  short: string;
  /** Name of the language, in itself. */
  name: string;
  og: string;
  dict: Dict;
}

// zh-Hant is the default and lives at `/`, as it does in the portal
// (routing.ts: "English is the secondary audience").
export const LOCALES: Record<Locale, LocaleInfo> = {
  "zh-Hant": {
    path: "/",
    lang: "zh-Hant-HK",
    short: "繁",
    name: "繁體中文",
    og: "/_site/og-zh-hant.png",
    dict: zhHant,
  },
  en: { path: "/en/", lang: "en", short: "EN", name: "English", og: "/_site/og-en.png", dict: en },
};

export const LOCALE_ORDER: Locale[] = ["zh-Hant", "en"];
