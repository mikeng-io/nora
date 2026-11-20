// The one list of served locales — plain data with no next-intl import, so a
// server module (the consent seam) can bind the same allowlist as the router
// without dragging the client navigation surface into it.
export const LOCALES = ["zh-Hant", "en"] as const;
export const DEFAULT_LOCALE = "zh-Hant";
export type Locale = (typeof LOCALES)[number];
