import type { PresenceRouteCorridor } from "@/lib/presence-schema";

// Pure formatting + naming for the presence surfaces (live map, analytics
// tabs, person-profile drawer) — the same role games-format.ts plays for
// the Pulse pages: one home so the same field can't render two ways across
// the map page, presence-analytics.tsx, and presence-profile.tsx.

// Same fixed-zone convention as games-format.ts's DISPLAY_TIME_ZONE (see its
// comment): every viewer of a shared group board reads the same wall-clock,
// and the rhythm bins are already bucketed server-side in this zone.
const DISPLAY_TIME_ZONE = "Asia/Hong_Kong";

export function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value);
}

export function formatDuration(ms: number): string {
  if (ms < 60 * 60 * 1000) return `${Math.max(1, Math.round(ms / (60 * 1000)))}m`;
  return `${(ms / (60 * 60 * 1000)).toFixed(1)}h`;
}

export function formatDate(ts: number, locale?: string): string {
  return new Intl.DateTimeFormat(locale, {
    timeZone: DISPLAY_TIME_ZONE, month: "short", day: "numeric",
  }).format(ts);
}

export function formatDateTime(ts: number, locale?: string): string {
  return new Intl.DateTimeFormat(locale, {
    timeZone: DISPLAY_TIME_ZONE,
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(ts);
}

/** "now" / "5m" / "3h" / "2d" — a fix's compact age, for rows that already
 * show the state word beside it. */
export function relativeAge(ageSeconds: number): string {
  if (ageSeconds < 60) return "now";
  const min = Math.round(ageSeconds / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  return `${Math.round(hr / 24)}d`;
}

/** A corridor's provisional display name: the ends of its address sequence
 * ("Nathan Road → Harbour City"). Corridors are directional — a reversed
 * sequence clusters as a different corridor — so from/to is honest, not
 * decorative. Returns null when the sequence is empty (address-less trips),
 * and the caller falls back to the raw corridor id. The stay-adjacency place
 * join ("Home → Work") is the intended upgrade; this is the v1 that needs no
 * new derivation. */
export function corridorEndpoints(
  corridor: Pick<PresenceRouteCorridor, "sequence">,
): { from: string; to: string } | null {
  const first = corridor.sequence[0];
  const last = corridor.sequence[corridor.sequence.length - 1];
  if (!first || !last) return null;
  // Life360 addresses are full strings ("Nathan Road, Tsim Sha Tsui, Hong
  // Kong") — the first segment is the legible part; the tail segments repeat
  // across every corridor in the same city.
  return { from: first.split(",")[0].trim(), to: last.split(",")[0].trim() };
}
