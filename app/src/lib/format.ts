// Every value the portal prints goes through here, so the same field can't
// render two ways on two pages. Absolute times are read in one fixed zone:
// a shared group board shows the same wall clock to everyone, and the
// rhythm bins are bucketed server-side in this zone already.
export const TIME_ZONE = "Asia/Hong_Kong";

const zh = (locale: string) => locale.startsWith("zh");

// The portal's zh-Hant is written Cantonese, so dates and relative times
// are formatted as Cantonese (ICU's `yue-Hant`: 聽日, 尋日, 星期日) rather
// than Mandarin (明天, 昨天, 週日). A browser without Cantonese data falls
// back to Hong Kong Chinese, which is still not Mandarin.
let yue: string | null = null;
export function intlLocale(locale: string): string {
  if (!zh(locale)) return locale;
  if (yue === null) {
    try {
      yue = Intl.DateTimeFormat.supportedLocalesOf(["yue-Hant"]).length ? "yue-Hant" : "zh-Hant-HK";
    } catch {
      yue = "zh-Hant-HK";
    }
  }
  return yue;
}

/** ICU's Cantonese mixes a few Mandarin forms and spells 而家 as 宜家; the
 * copy everywhere else in the portal uses these. */
const CANTONESE_FIXES: Array<[RegExp, string]> = [
  [/宜家/g, "而家"],
  [/後天/g, "後日"],
  [/前天/g, "前日"],
];
function cantonese(text: string, locale: string): string {
  return zh(locale) ? CANTONESE_FIXES.reduce((t, [re, to]) => t.replace(re, to), text) : text;
}

// ----------------------------------------------------------- durations

interface Units {
  d: string;
  h: string;
  m: string;
  s: string;
  sep: string;
}

function units(locale: string): Units {
  return zh(locale)
    ? { d: "日", h: "小時", m: "分", s: "秒", sep: "" }
    : { d: "d", h: "h", m: "m", s: "s", sep: " " };
}

/** 3d 4h · 2h 5m · 12m · 40s — two units at most, the larger first. */
export function formatDuration(ms: number, locale: string): string {
  const u = units(locale);
  if (!Number.isFinite(ms) || ms < 0) ms = 0;
  const totalSec = Math.floor(ms / 1000);
  const d = Math.floor(totalSec / 86400);
  const h = Math.floor((totalSec % 86400) / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  if (d > 0) return h > 0 ? `${d}${u.d}${u.sep}${h}${u.h}` : `${d}${u.d}`;
  if (h > 0) return m > 0 ? `${h}${u.h}${u.sep}${m}${u.m}` : `${h}${u.h}`;
  if (m > 0) return `${m}${u.m}`;
  return `${totalSec}${u.s}`;
}

/** Hours with one decimal, for totals ("12.5h" / "12.5 小時"). */
export function formatHours(ms: number, locale: string): string {
  const hours = ms / 3_600_000;
  const n = new Intl.NumberFormat(locale, { maximumFractionDigits: hours < 10 ? 1 : 0 }).format(
    hours,
  );
  return zh(locale) ? `${n} 小時` : `${n}h`;
}

// ------------------------------------------------------------- relative

const rtfCache = new Map<string, Intl.RelativeTimeFormat>();
function rtf(locale: string, style: "long" | "narrow") {
  const key = `${locale}:${style}`;
  let f = rtfCache.get(key);
  if (!f) {
    f = new Intl.RelativeTimeFormat(intlLocale(locale), { numeric: "auto", style });
    rtfCache.set(key, f);
  }
  return f;
}

/** "just now" / "5 minutes ago" / "in 2 hours" / "yesterday". */
export function formatRelative(
  ts: number,
  locale: string,
  now = Date.now(),
  style: "long" | "narrow" = "long",
): string {
  const diffSec = Math.round((ts - now) / 1000);
  const abs = Math.abs(diffSec);
  const f = rtf(locale, style);
  let out: string;
  if (abs < 45) out = f.format(0, "second");
  else if (abs < 45 * 60) out = f.format(Math.round(diffSec / 60), "minute");
  else if (abs < 22 * 3600) out = f.format(Math.round(diffSec / 3600), "hour");
  // Calendar days in the display zone, so "yesterday" means yesterday.
  else if (abs < 6 * 86400) out = f.format(dayIndex(ts) - dayIndex(now), "day");
  else if (abs < 30 * 86400) out = f.format(Math.round(diffSec / 604800), "week");
  else return formatDate(ts, locale);
  return cantonese(out, locale);
}

/** A compact age for tight rows: "now", "4m", "3h", "2d". */
export function formatAge(ageSeconds: number, locale: string): string {
  const u = units(locale);
  if (ageSeconds < 60) return zh(locale) ? "啱啱" : "now";
  const min = Math.round(ageSeconds / 60);
  if (min < 60) return `${min}${u.m}`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}${u.h}`;
  return `${Math.round(hr / 24)}${u.d}`;
}

// ------------------------------------------------------------- absolute

const dtfCache = new Map<string, Intl.DateTimeFormat>();
function dtf(locale: string, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${locale}:${JSON.stringify(opts)}`;
  let f = dtfCache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat(intlLocale(locale), { timeZone: TIME_ZONE, ...opts });
    dtfCache.set(key, f);
  }
  return f;
}

export const formatTime = (ts: number, locale: string) =>
  dtf(locale, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(ts);

export const formatDate = (ts: number, locale: string) =>
  dtf(locale, { month: "short", day: "numeric" }).format(ts);

export const formatWeekdayDate = (ts: number, locale: string) =>
  dtf(locale, { weekday: "short", month: "short", day: "numeric" }).format(ts);

export const formatLongDate = (ts: number, locale: string) =>
  dtf(locale, { weekday: "long", month: "long", day: "numeric" }).format(ts);

export const formatDateTime = (ts: number, locale: string) =>
  dtf(locale, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(ts);

export const formatWeekday = (ts: number, locale: string, style: "short" | "long" = "short") =>
  dtf(locale, { weekday: style }).format(ts);

/** Days since the epoch in the display zone: two timestamps are the same
 * calendar day when these match. */
export function dayIndex(ts: number): number {
  const parts = dtf("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" }).format(ts);
  return Math.floor(Date.parse(`${parts}T00:00:00Z`) / 86_400_000);
}

/** The display zone's hour (0..23) and weekday (0 = Monday) for a time. */
export function zonedParts(ts: number): { hour: number; weekday: number; minute: number } {
  const parts = dtf("en-US", {
    weekday: "short",
    hour: "numeric",
    minute: "numeric",
    hourCycle: "h23",
  }).formatToParts(ts);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(get("weekday"));
  return { hour: Number(get("hour")) % 24, minute: Number(get("minute")), weekday };
}

/** Part of the day in the display zone, for a greeting. */
export function partOfDay(ts: number): "morning" | "afternoon" | "evening" | "night" {
  const { hour } = zonedParts(ts);
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 18) return "afternoon";
  if (hour >= 18 && hour < 23) return "evening";
  return "night";
}

// --------------------------------------------------------------- numbers

export const formatNumber = (n: number, locale: string, digits = 0) =>
  Number.isFinite(n)
    ? new Intl.NumberFormat(intlLocale(locale), { maximumFractionDigits: digits }).format(n)
    : "—";

/** A date-time format in the portal's zone and the reader's language, for
 * the few places that need options the helpers above do not cover. */
export const dateFormat = (locale: string, opts: Intl.DateTimeFormatOptions) => dtf(locale, opts);

export const formatPercent = (fraction: number, locale: string) =>
  Number.isFinite(fraction)
    ? new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 }).format(fraction)
    : "—";

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "0 B";
  const unitsList = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < unitsList.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(value >= 100 || i === 0 ? 0 : 1)} ${unitsList[i]}`;
}

export const formatRate = (bps: number) => `${formatBytes(bps)}/s`;

export function formatDistance(meters: number, locale: string): string {
  if (meters < 1000) return `${formatNumber(Math.round(meters / 10) * 10, locale)} m`;
  return `${formatNumber(meters / 1000, locale, meters < 10_000 ? 1 : 0)} km`;
}

export const formatSpeed = (kmh: number, locale: string) =>
  `${formatNumber(kmh, locale)} km/h`;

// --------------------------------------------------------------- places

/** A Life360 address is a full string ("Nathan Road, Mong Kok, Hong Kong");
 * the first part is the legible one, the tail repeats across the city. */
export function shortAddress(address: string | undefined | null): string | null {
  if (!address) return null;
  const first = address.split(",")[0]?.trim();
  return first || null;
}

/** "Nathan Road, Mong Kok" — the first two parts, for a second line. */
export function mediumAddress(address: string | undefined | null): string | null {
  if (!address) return null;
  const parts = address
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .filter((p) => !/^hong kong$|^香港$/i.test(p));
  return parts.slice(0, 2).join(", ") || null;
}
