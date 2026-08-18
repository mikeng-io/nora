// Pure formatting for Pulse's raw container metrics. Duration units follow
// the reader's locale (matches game-server-monitor's own
// web/src/lib/format.ts convention) so a duration never mixes English units
// into an otherwise-Chinese sentence.

type DurationUnits = { d: string; h: string; m: string; sep: string };

function unitsFor(locale?: string): DurationUnits {
  return locale?.startsWith("zh")
    ? { d: " 天", h: " 小時", m: " 分鐘", sep: " " }
    : { d: "d", h: "h", m: "m", sep: " " };
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex++;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

export function formatRate(bytesPerSecond: number): string {
  if (!Number.isFinite(bytesPerSecond) || bytesPerSecond < 0) return "0 B/s";
  return `${formatBytes(bytesPerSecond)}/s`;
}

export function formatCpuPercent(percent: number): string {
  if (!Number.isFinite(percent)) return "—";
  return `${percent.toFixed(1)}%`;
}

export function formatUptime(seconds: number, locale?: string): string {
  const u = unitsFor(locale);
  if (!Number.isFinite(seconds) || seconds < 0) return `0${u.m}`;
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}${u.d}${u.sep}${h}${u.h}`;
  if (h > 0) return `${h}${u.h}${u.sep}${m}${u.m}`;
  return `${m}${u.m}`;
}

/** Uptime derived from the sample's own clock, so SSR and hydration agree. */
export function uptimeSecondsFrom(current: { timestamp: number; startedAt?: string }): number {
  if (!current.startedAt) return 0;
  const started = new Date(current.startedAt).getTime();
  if (Number.isNaN(started)) return 0;
  return Math.max(0, Math.floor((current.timestamp - started) / 1000));
}

/** A millisecond duration (Pulse's `*Ms` fields) through the same
 * day/hour/minute ladder `formatUptime` already uses for seconds. */
export function formatDurationMs(ms: number, locale?: string): string {
  if (!Number.isFinite(ms) || ms < 0) return formatUptime(0, locale);
  return formatUptime(Math.floor(ms / 1000), locale);
}

export function formatPercent(fraction: number): string {
  if (!Number.isFinite(fraction)) return "—";
  return `${(fraction * 100).toFixed(0)}%`;
}

export function formatCount(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return new Intl.NumberFormat().format(value);
}

/** "3m ago" / "in 2h" — a client-clock-relative read of an epoch-ms
 * timestamp, in the reader's own register (auto-picks minute vs. hour). */
export function formatRelativeTime(timestampMs: number, locale?: string): string {
  const diffMs = timestampMs - Date.now();
  const diffMin = Math.round(diffMs / 60_000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (Math.abs(diffMin) < 60) return rtf.format(diffMin, "minute");
  const diffHour = Math.round(diffMin / 60);
  if (Math.abs(diffHour) < 24) return rtf.format(diffHour, "hour");
  const diffDay = Math.round(diffHour / 24);
  return rtf.format(diffDay, "day");
}

/** A fixed-timezone absolute clock read, deliberately NOT the viewer's own
 * timezone — matches game-server-monitor's own reasoning (its
 * DISPLAY_TIME_ZONE constant): every viewer of a shared group board should
 * read the same wall-clock time regardless of where they personally are,
 * and a server-rendered/client-hydrated absolute time needs one canonical
 * zone anyway to avoid an SSR/CSR mismatch. */
const DISPLAY_TIME_ZONE = "Asia/Hong_Kong";

export function formatClockTime(timestampMs: number, locale?: string): string {
  return new Intl.DateTimeFormat(locale, {
    timeZone: DISPLAY_TIME_ZONE, hour: "2-digit", minute: "2-digit",
  }).format(timestampMs);
}

export function formatDateTime(timestampMs: number, locale?: string): string {
  return new Intl.DateTimeFormat(locale, {
    timeZone: DISPLAY_TIME_ZONE, month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  }).format(timestampMs);
}
