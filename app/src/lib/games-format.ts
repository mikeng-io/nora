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

export function formatGb(gb: number): string {
  if (!Number.isFinite(gb) || gb < 0) return "0 GB";
  return `${gb.toFixed(1)} GB`;
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
